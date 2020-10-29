from __future__ import absolute_import, print_function

import time
import signal
import os
import click
from six import text_type

from sentry.utils.compat import map

import requests


def get_docker_client():
    import docker

    client = docker.from_env()
    try:
        client.ping()
        return client
    except Exception:
        raise click.ClickException("Make sure Docker is running.")


def get_local_image_digest(repo, tag):
    import docker

    with docker.APIClient() as docker_low_level_client:
        local_image_details = docker_low_level_client.inspect_image(
            "{repo}:{tag}".format(repo=repo, tag=tag)
        )

    digest = local_image_details["RepoDigests"][0]
    i = digest.index("@")
    return digest[i + 1 :]


def get_docker_registry_token(*, registry="default", repo):
    resp = requests.get(
        {
            "default": "https://auth.docker.io/token?service=registry.docker.io",
            "us.gcr.io": "https://us.gcr.io/v2/token?service=gcr.io",
        }[registry]
        + "&scope=repository:{repo}:pull".format(repo=repo,)
    )
    resp.raise_for_status()

    # XXX: We don't take into account expires_in / token refresh.
    return resp.json()["token"]


def get_remote_image_digest(*, registry="default", repo, tag):
    registry = "default"
    if "/" not in repo:
        # If it's indeed the default (official) registry,
        # then we need to prepend library/ here.
        repo = "library/" + repo
    else:
        tokens = repo.split("/")
        if "." in tokens[0]:
            # Probably a domain name, so assume it's the registry.
            registry = tokens[0]
            repo = "/".join(tokens[1:])

    registry_token = get_docker_registry_token(registry=registry, repo=repo)
    resp = requests.head(
        {"default": "https://index.docker.io/v2/", "us.gcr.io": "https://us.gcr.io/v2/"}[registry]
        + "{repo}/manifests/{tag}".format(repo=repo, tag=tag),
        headers={
            "Authorization": "Bearer " + registry_token,
            "Accept": "application/vnd.docker.distribution.manifest.v2+json",
        },
    )
    resp.raise_for_status()
    return resp.headers["Docker-Content-Digest"]


def get_or_create(client, thing, name):
    from docker.errors import NotFound

    try:
        return getattr(client, thing + "s").get(name)
    except NotFound:
        click.secho("> Creating '%s' %s" % (name, thing), err=True, fg="yellow")
        return getattr(client, thing + "s").create(name)


def ensure_interface(ports):
    # If there is no interface specified, make sure the
    # default interface is 127.0.0.1
    rv = {}
    for k, v in ports.items():
        if not isinstance(v, tuple):
            v = ("127.0.0.1", v)
        rv[k] = v
    return rv


@click.group()
@click.pass_context
def devservices(ctx):
    """
    Manage dependent development services required for Sentry.

    Do not use in production!
    """
    ctx.obj["client"] = get_docker_client()

    # Disable backend validation so no devservices commands depend on like,
    # redis to be already running.
    os.environ["SENTRY_SKIP_BACKEND_VALIDATION"] = "1"


@devservices.command()
@click.option("--project", default="sentry")
@click.option("--fast", is_flag=True, default=False, help="Never pull and reuse containers.")
@click.argument("service", nargs=1)
@click.pass_context
def attach(ctx, project, fast, service):
    """
    Run a single devservice in foreground, as opposed to `up` which runs all of
    them in the background.

    Accepts a single argument, the name of the service to spawn. The service
    will run with output printed to your terminal, and the ability to kill it
    with ^C. This is used in devserver.

    Note: This does not update images, you will have to use `devservices up`
    for that.
    """
    from sentry.runner import configure

    configure()

    containers = _prepare_containers(project, silent=True)
    if service not in containers:
        raise click.ClickException("Service `{}` is not known or not enabled.".format(service))

    container = _start_service(
        ctx.obj["client"], service, containers, project, fast=fast, always_start=True
    )

    def exit_handler(*_):
        try:
            click.echo("Stopping {}".format(service))
            container.stop()
            click.echo("Removing {}".format(service))
            container.remove()
        except KeyboardInterrupt:
            pass

    signal.signal(signal.SIGINT, exit_handler)
    signal.signal(signal.SIGTERM, exit_handler)

    for line in container.logs(stream=True, since=int(time.time() - 20)):
        click.echo(line, nl=False)


@devservices.command()
@click.argument("services", nargs=-1)
@click.option("--project", default="sentry")
@click.option("--exclude", multiple=True, help="Service to ignore and not run. Repeatable option.")
@click.option("--fast", is_flag=True, default=False, help="Never pull and reuse containers.")
@click.pass_context
def up(ctx, services, project, exclude, fast):
    """
    Run/update dependent services.

    The default is everything, however you may pass positional arguments to specify
    an explicit list of services to bring up.

    You may also exclude services, for example: --exclude redis --exclude postgres.
    """
    from sentry.runner import configure

    configure()

    containers = _prepare_containers(project, silent=True)
    selected_services = set()

    if services:
        for service in services:
            if service not in containers:
                click.secho(
                    "Service `{}` is not known or not enabled.\n".format(service),
                    err=True,
                    fg="red",
                )
                click.secho(
                    "Services that are available:\n" + "\n".join(containers.keys()) + "\n", err=True
                )
                raise click.Abort()
            selected_services.add(service)
    else:
        selected_services = set(containers.keys())

    for service in exclude:
        if service not in containers:
            click.secho(
                "Service `{}` is not known or not enabled.\n".format(service), err=True, fg="red"
            )
            click.secho(
                "Services that are available:\n" + "\n".join(containers.keys()) + "\n", err=True
            )
            raise click.Abort()
        selected_services.remove(service)

    if fast:
        click.secho(
            "> Warning! Fast mode completely eschews any image updating, so services may be stale.",
            err=True,
            fg="red",
        )

    get_or_create(ctx.obj["client"], "network", project)

    for name in selected_services:
        _start_service(ctx.obj["client"], name, containers, project, fast=fast)


def _prepare_containers(project, silent=False):
    from django.conf import settings
    from sentry import options as sentry_options

    containers = {}

    for name, options in settings.SENTRY_DEVSERVICES.items():
        options = options.copy()
        test_fn = options.pop("only_if", None)
        if test_fn and not test_fn(settings, sentry_options):
            if not silent:
                click.secho(
                    "! Skipping {} due to only_if condition".format(name), err=True, fg="cyan"
                )
            continue

        options["network"] = project
        options["detach"] = True
        options["name"] = project + "_" + name
        options.setdefault("ports", {})
        options.setdefault("environment", {})
        # set policy to unless-stopped to avoid automatically restarting containers on boot
        # this is important given you can start multiple sets of containers that can conflict
        # with each other
        options.setdefault("restart_policy", {"Name": "unless-stopped"})
        options["ports"] = ensure_interface(options["ports"])
        containers[name] = options

    # keys are service names
    # a service has 1 container exactly, the container name being value["name"]
    return containers


def _start_service(client, name, containers, project, fast=False, always_start=False):
    from django.conf import settings
    from docker.errors import NotFound

    options = containers[name]

    # HACK(mattrobenolt): special handle snuba backend because it needs to
    # handle different values based on the eventstream backend
    # For snuba, we can't run the full suite of devserver, but can only
    # run the api.
    if name == "snuba" and "snuba" in settings.SENTRY_EVENTSTREAM:
        options["environment"].pop("DEFAULT_BROKERS", None)
        options["command"] = ["devserver", "--no-workers"]

    for key, value in list(options["environment"].items()):
        options["environment"][key] = value.format(containers=containers)

    pull = options.pop("pull", False)
    if not fast:
        repo, tag = options["image"].split(":")

        local_image_digest = None
        try:
            local_image_digest = get_local_image_digest(repo=repo, tag=tag)
        except NotFound:
            click.secho("> Image '%s' not found locally, pulling." % options["image"], fg="yellow")
            client.images.pull(options["image"])
            local_image_digest = get_local_image_digest(repo=repo, tag=tag)

        remote_image_digest = get_remote_image_digest(repo=repo, tag=tag)

        print("local", options["image"], local_image_digest)
        print("remote", options["image"], remote_image_digest)

        # if pull:
        #    click.secho("> Pulling image '%s'" % options["image"], err=True, fg="green")
        #    client.images.pull(options["image"])
        # else:
        #    # We want make sure to pull everything on the first time,
        #    # (the image doesn't exist), regardless of pull=True.
        #    try:
        #        client.images.get(options["image"])
        #    except NotFound:
        #        click.secho("> Pulling image '%s'" % options["image"], err=True, fg="green")
        #        client.images.pull(options["image"])

    return

    for mount in list(options.get("volumes", {}).keys()):
        if "/" not in mount:
            get_or_create(client, "volume", project + "_" + mount)
            options["volumes"][project + "_" + mount] = options["volumes"].pop(mount)

    listening = ""
    if options["ports"]:
        listening = "(listening: %s)" % ", ".join(map(text_type, options["ports"].values()))

    # If a service is associated with the devserver, then do not run the created container.
    # This was mainly added since it was not desirable for nginx to occupy port 8000 on the
    # first "devservices up".
    # Nowadays that nginx is gone again, it's still nice to be able to shut
    # down services within devserver.
    # See https://github.com/getsentry/sentry/pull/18362#issuecomment-616785458
    with_devserver = options.pop("with_devserver", False)

    # Two things call _start_service.
    # devservices up, and devservices attach.
    # Containers that should be started on-demand with devserver
    # should ONLY be started via the latter, which sets `always_start`.
    if with_devserver and not always_start:
        click.secho(
            "> Not starting container '%s' because it should be started on-demand with devserver."
            % options["name"],
            fg="yellow",
        )
        # XXX: if always_start=False, do not expect to have a container returned 100% of the time.
        return None

    container = None
    try:
        container = client.containers.get(options["name"])
    except NotFound:
        pass

    if container is not None:
        # devservices which are marked with pull True will need their containers
        # to be recreated with the freshly pulled image.
        should_reuse_container = not pull

        # Except if the container is started as part of devserver we should reuse it.
        # Or, if we're in fast mode (devservices up --fast)
        if with_devserver or fast:
            should_reuse_container = True

        if should_reuse_container:
            click.secho(
                "> Starting EXISTING container '%s' %s" % (container.name, listening),
                err=True,
                fg="yellow",
            )
            # Note that if the container is already running, this will noop.
            # This makes repeated `devservices up` quite fast.
            container.start()
            return container

        click.secho("> Stopping container '%s'" % container.name, err=True, fg="yellow")
        container.stop()
        click.secho("> Removing container '%s'" % container.name, err=True, fg="yellow")
        container.remove()

    click.secho("> Creating container '%s'" % options["name"], err=True, fg="yellow")
    container = client.containers.create(**options)
    click.secho("> Starting container '%s' %s" % (container.name, listening), err=True, fg="yellow")
    container.start()
    return container


@devservices.command()
@click.option("--project", default="sentry")
@click.argument("service", nargs=-1)
@click.pass_context
def down(ctx, project, service):
    """
    Shut down services without deleting their underlying containers and data.
    Useful if you want to temporarily relieve resources on your computer.

    The default is everything, however you may pass positional arguments to specify
    an explicit list of services to bring down.
    """
    prefix = project + "_"

    # TODO: make more like devservices rm

    for container in ctx.obj["client"].containers.list(all=True):
        if container.name.startswith(prefix):
            if not service or container.name[len(prefix) :] in service:
                click.secho("> Stopping '%s' container" % container.name, err=True, fg="red")
                container.stop()


@devservices.command()
@click.option("--project", default="sentry")
@click.argument("services", nargs=-1)
@click.pass_context
def rm(ctx, project, services):
    """
    Shut down and delete all services and associated data.
    Useful if you'd like to start with a fresh slate.

    The default is everything, however you may pass positional arguments to specify
    an explicit list of services to remove.
    """
    from docker.errors import NotFound

    from sentry.runner import configure

    configure()

    containers = _prepare_containers(project, silent=True)

    if services:
        selected_containers = {}
        for service in services:
            # XXX: This code is also fairly duplicated in here at this point, so dedupe in the future.
            if service not in containers:
                click.secho(
                    "Service `{}` is not known or not enabled.\n".format(service),
                    err=True,
                    fg="red",
                )
                click.secho(
                    "Services that are available:\n" + "\n".join(containers.keys()) + "\n", err=True
                )
                raise click.Abort()
            selected_containers[service] = containers[service]
        containers = selected_containers

    click.confirm(
        """
This will delete these services and all of their data:

%s

Are you sure you want to continue?"""
        % "\n".join(containers.keys()),
        abort=True,
    )

    for service_name, container_options in containers.items():
        try:
            container = ctx.obj["client"].containers.get(container_options["name"])
        except NotFound:
            click.secho(
                "> WARNING: non-existent container '%s'" % container_options["name"],
                err=True,
                fg="yellow",
            )
            continue

        click.secho("> Stopping '%s' container" % container_options["name"], err=True, fg="red")
        container.stop()
        click.secho("> Removing '%s' container" % container_options["name"], err=True, fg="red")
        container.remove()

    prefix = project + "_"

    for volume in ctx.obj["client"].volumes.list():
        if volume.name.startswith(prefix):
            if not services or volume.name[len(prefix) :] in services:
                click.secho("> Removing '%s' volume" % volume.name, err=True, fg="red")
                volume.remove()

    if not services:
        try:
            network = ctx.obj["client"].networks.get(project)
        except NotFound:
            pass
        else:
            click.secho("> Removing '%s' network" % network.name, err=True, fg="red")
            network.remove()
