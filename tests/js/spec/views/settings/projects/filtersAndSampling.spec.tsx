import {Fragment} from 'react';

import {initializeOrg} from 'sentry-test/initializeOrg';
import {
  BoundFunctions,
  FindByRole,
  fireEvent,
  mountWithTheme,
  waitForElementToBeRemoved,
  within,
} from 'sentry-test/reactTestingLibrary';
import {findByTextContent} from 'sentry-test/utils';

import GlobalModal from 'app/components/globalModal';
import FiltersAndSampling from 'app/views/settings/project/filtersAndSampling';
import {DYNAMIC_SAMPLING_DOC_LINK} from 'app/views/settings/project/filtersAndSampling/utils';

describe('Filters and Sampling', function () {
  const commonConditionCategories = [
    'Releases',
    'Environments',
    'User Id',
    'User Segment',
    'Browser Extensions',
    'Localhost',
    'Legacy Browsers',
    'Web Crawlers',
    'IP Addresses',
    'Content Security Policy',
    'Error Messages',
    'Transactions',
  ];

  // @ts-expect-error
  MockApiClient.addMockResponse({
    url: '/projects/org-slug/project-slug/',
    method: 'GET',
    // @ts-expect-error
    body: [TestStubs.Project()],
  });

  // @ts-expect-error
  MockApiClient.addMockResponse({
    url: '/projects/org-slug/project-slug/',
    method: 'PUT',
    body: [
      // @ts-expect-error
      TestStubs.Project({
        dynamicSampling: {
          rules: [
            {
              sampleRate: 0.2,
              type: 'error',
              condition: {
                op: 'and',
                inner: [
                  {
                    op: 'glob',
                    name: 'event.release',
                    value: ['1.2.3'],
                  },
                ],
              },
              id: 39,
            },
          ],
          next_id: 40,
        },
      }),
    ],
  });

  function renderComponent(withModal = true) {
    const {organization, project} = initializeOrg({
      organization: {features: ['filters-and-sampling']},
    } as Parameters<typeof initializeOrg>[0]);

    return mountWithTheme(
      <Fragment>
        {withModal && <GlobalModal />}
        <FiltersAndSampling organization={organization} project={project} />
      </Fragment>
    );
  }

  async function renderModal(
    screen: BoundFunctions<{findByRole: FindByRole}>,
    actionElement: HTMLElement,
    takeScreenshot = false
  ) {
    // Open Modal
    fireEvent.click(actionElement);
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();

    if (takeScreenshot) {
      expect(dialog).toSnapshot();
    }

    return within(dialog);
  }

  it('renders', async function () {
    const wrapper = renderComponent(false);
    const {container, getByRole, getByText, queryAllByText} = wrapper;

    // Title
    expect(getByText('Filters & Sampling')).toBeTruthy();

    // Error rules container
    expect(
      await findByTextContent(
        wrapper,
        'Manage the inbound data you want to store. To change the sampling rate or rate limits, update your SDK configuration. The rules added below will apply on top of your SDK configuration. Any new rule may take a few minutes to propagate.'
      )
    ).toBeTruthy();

    expect(
      getByRole('link', {
        name: 'update your SDK configuration',
      })
    ).toHaveAttribute('href', DYNAMIC_SAMPLING_DOC_LINK);

    expect(getByText('There are no error rules to display')).toBeTruthy();
    expect(getByText('Add error rule')).toBeTruthy();

    // Transaction traces and individual transactions rules container
    expect(
      getByText('Rules for traces should precede rules for individual transactions.')
    ).toBeTruthy();

    expect(getByText('There are no transaction rules to display')).toBeTruthy();
    expect(getByText('Add transaction rule')).toBeTruthy();

    expect(queryAllByText('Read the docs')).toHaveLength(2);

    expect(container).toSnapshot();
  });

  describe('error rule modal', function () {
    it('renders modal', async function () {
      const component = renderComponent();
      const {getByText, getByLabelText} = component;

      // Open Modal
      const modal = await renderModal(component, getByText('Add error rule'), true);

      // Modal content
      expect(modal.getByText('Add Error Sampling Rule')).toBeTruthy();
      expect(modal.queryByText('Tracing')).toBeFalsy();
      expect(modal.getByText('Conditions')).toBeTruthy();
      expect(modal.getByText('Add Condition')).toBeTruthy();
      expect(modal.getByText('Apply sampling rate to all errors')).toBeTruthy();
      expect(modal.getByText('Sampling Rate \u0025')).toBeTruthy();
      expect(modal.getByPlaceholderText('\u0025')).toHaveValue(null);
      expect(modal.getByRole('button', {name: 'Cancel'})).toBeTruthy();
      const saveRuleButton = modal.getByRole('button', {name: 'Save Rule'});
      expect(saveRuleButton).toBeTruthy();
      expect(saveRuleButton).toBeDisabled();

      // Close Modal
      fireEvent.click(getByLabelText('Close Modal'));
      await waitForElementToBeRemoved(() => getByText('Add Error Sampling Rule'));
    });

    it('condition options', async function () {
      const component = renderComponent();
      const {getByText, findByTestId, getByLabelText} = component;

      // Open Modal
      const modal = await renderModal(component, getByText('Add error rule'));

      // Click on 'Add condition'
      fireEvent.click(modal.getByText('Add Condition'));

      // Autocomplete
      const autoCompleteList = await findByTestId('autocomplete-list');
      expect(autoCompleteList).toBeInTheDocument();

      // Condition Options
      const conditionOptions = modal.queryAllByRole('presentation');
      expect(conditionOptions).toHaveLength(commonConditionCategories.length);

      for (const conditionOptionIndex in conditionOptions) {
        expect(conditionOptions[conditionOptionIndex].textContent).toEqual(
          commonConditionCategories[conditionOptionIndex]
        );
      }

      // Close Modal
      fireEvent.click(getByLabelText('Close Modal'));
      await waitForElementToBeRemoved(() => getByText('Add Error Sampling Rule'));
    });

    it.only('save rule', async function () {
      const component = renderComponent();
      const {getByText, queryByText, findByTestId, getByPlaceholderText} = component;

      // Open Modal
      const modal = await renderModal(component, getByText('Add error rule'));

      // Click on 'Add condition'
      fireEvent.click(modal.getByText('Add Condition'));

      // Autocomplete
      const autoCompleteList = await findByTestId('autocomplete-list');
      expect(autoCompleteList).toBeInTheDocument();

      // Condition Options
      const conditionOptions = modal.queryAllByRole('presentation');

      // Click on the first condition option
      fireEvent.click(conditionOptions[0]);

      // Release Field
      const releaseField = getByPlaceholderText('ex. 1* or [I3].[0-9].* (Multiline)');
      expect(releaseField).toBeTruthy();

      // Fill release field
      fireEvent.change(releaseField, {target: {value: '1.2.3'}});

      // Button is still disabled
      const saveRuleButton = modal.getByRole('button', {name: 'Save Rule'});
      expect(saveRuleButton).toBeTruthy();
      expect(saveRuleButton).toBeDisabled();

      // Fill sample rate field
      const sampleRateField = modal.getByPlaceholderText('\u0025');
      expect(sampleRateField).toBeTruthy();
      fireEvent.change(sampleRateField, {target: {value: 20}});

      expect(saveRuleButton).toBeEnabled();

      // Click on save button
      fireEvent.click(saveRuleButton);

      // Modal will close
      await waitForElementToBeRemoved(() => getByText('Add Error Sampling Rule'));

      // Error rules panel is updated
      expect(queryByText('There are no error rules to display')).toBeFalsy();
    });
  });

  describe('transaction rule modal', function () {
    const conditionTracingCategories = [
      'Releases',
      'Environments',
      'User Id',
      'User Segment',
      'Transactions',
    ];

    it('renders modal', async function () {
      const component = renderComponent();
      const {getByText, getByLabelText} = component;

      // Open Modal
      const modal = await renderModal(component, getByText('Add transaction rule'), true);

      // Modal content
      expect(modal.getByText('Add Transaction Sampling Rule')).toBeTruthy();
      expect(modal.getByText('Tracing')).toBeTruthy();
      expect(modal.getByRole('checkbox')).toBeChecked();
      expect(
        await findByTextContent(
          modal,
          'Include all related transactions by trace ID. This can span across multiple projects. All related errors will remain. Learn more about tracing.'
        )
      ).toBeTruthy();
      expect(
        modal.getByRole('link', {
          name: 'Learn more about tracing',
        })
      ).toHaveAttribute('href', DYNAMIC_SAMPLING_DOC_LINK);
      expect(modal.getByText('Conditions')).toBeTruthy();
      expect(modal.getByText('Add Condition')).toBeTruthy();
      expect(modal.getByText('Apply sampling rate to all transactions')).toBeTruthy();
      expect(modal.getByText('Sampling Rate \u0025')).toBeTruthy();
      expect(modal.getByPlaceholderText('\u0025')).toHaveValue(null);
      expect(modal.getByRole('button', {name: 'Cancel'})).toBeTruthy();
      const saveRuleButton = modal.getByRole('button', {name: 'Save Rule'});
      expect(saveRuleButton).toBeTruthy();
      expect(saveRuleButton).toBeDisabled();

      // Close Modal
      fireEvent.click(getByLabelText('Close Modal'));
      await waitForElementToBeRemoved(() => getByText('Add Transaction Sampling Rule'));
    });

    it('condition options', async function () {
      const component = renderComponent();
      const {getByText, getByLabelText} = component;

      // Open Modal
      const modal = await renderModal(component, getByText('Add transaction rule'));

      // Click on 'Add condition'
      fireEvent.click(modal.getByText('Add Condition'));

      // Autocomplete
      const autoCompleteList = await modal.findByTestId('autocomplete-list');
      expect(autoCompleteList).toBeInTheDocument();

      // Trancing Condition Options
      const conditionTracingOptions = modal.queryAllByRole('presentation');
      expect(conditionTracingOptions).toHaveLength(conditionTracingCategories.length);

      for (const conditionTracingOptionIndex in conditionTracingOptions) {
        expect(conditionTracingOptions[conditionTracingOptionIndex].textContent).toEqual(
          conditionTracingCategories[conditionTracingOptionIndex]
        );
      }

      // Uncheck tracing checkbox
      fireEvent.click(modal.getByRole('checkbox'));

      // Click on 'Add condition'
      fireEvent.click(modal.getByText('Add Condition'));

      // No Tracing Condition Options
      const conditionOptions = modal.queryAllByRole('presentation');
      expect(conditionOptions).toHaveLength(commonConditionCategories.length);

      for (const conditionOptionIndex in conditionOptions) {
        expect(conditionOptions[conditionOptionIndex].textContent).toEqual(
          commonConditionCategories[conditionOptionIndex]
        );
      }

      // Close Modal
      fireEvent.click(getByLabelText('Close Modal'));
      await waitForElementToBeRemoved(() => getByText('Add Transaction Sampling Rule'));
    });
  });
});
