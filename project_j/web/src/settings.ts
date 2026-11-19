// The settings page's pure parts: labels and rows for the waiting times and model bindings (ADR-0025).
type Body = Record<string, any>

export const deadlineLabels: Record<string, string> = {
  classroom_judgment: 'Classroom judgment', vocabulary: 'Word meanings', evaluation: 'Evaluation', planning: 'Planning',
  preparation_drafting: 'Preparation: drafting', preparation_image: 'Preparation: pictures', preparation_animation: 'Preparation: animations',
  preparation_image_check: 'Preparation: picture checks',
}

export const PURPOSES = ['planning', 'preparation', 'classroom_judgment', 'evaluation', 'vocabulary'] as const
export const ADAPTERS = ['codex', 'simulated'] as const  // mirrors ADAPTERS in platform/models/registry.py
export const EFFORTS = ['low', 'medium', 'high', 'xhigh'] as const
export const TIERS = ['default', 'fast'] as const

export type BindingRow = { purpose: string, adapter: string, model: string, effort: string, tier: string }

/** One row per purpose in workflow order; a purpose without a binding shows blanks. */
export function bindingRows(models?: Body): BindingRow[] {
  return PURPOSES.map(purpose => {
    const binding = models?.purposes?.[purpose]?.binding ?? {}
    return { purpose, adapter: binding.adapter ?? '', model: binding.model ?? '', effort: binding.effort ?? '', tier: binding.tier ?? '' }
  })
}

/** The choices with the current value kept first when they lack it (blank too), so a select shows what will be saved. */
export const withCurrent = (values: readonly string[], current: string): readonly string[] =>
  values.includes(current) ? values : [current, ...values]

/** A row's model choices: what its adapter offers now, keeping the saved model; none (type it) when there is no list. */
export function modelOptions(offered: Record<string, string[]>, row: BindingRow): readonly string[] | undefined {
  const models = offered[row.adapter]
  return models?.length ? withCurrent(models, row.model) : undefined
}

export function probeNotes(models: Body | undefined): string[] {
  return models?.initialized ? [] : ['This setup has not passed the probe; classes, preparation, evaluation and planning are refused until it does.']
}

// The classroom judgment reference set for the current setup (dashboard judgment_check; ADR-0032).
type Check = Body | null | undefined

export function checkLine(check: Body): string {
  const report = check.report ?? {}
  return {
    not_run: 'Not run for this setup yet.',
    passed: `Passed (${report.passed}/${report.total}${report.settled_by_rerun ? ', settled by rerun' : ''}).`,
    failed: 'Failed: some answers were judged wrong. See below.',
    needs_rerun: 'Some calls failed; rerun those questions.',
  }[check.state as string] ?? check.state
}

export const checkConfirm = (check: Body): string =>
  `This asks ${check.cases} questions to ${check.binding.model} through your ChatGPT subscription and takes about ${Math.ceil(check.estimate_seconds / 60)} minutes. `
  + 'Classes cannot start while it runs; evaluations and planning wait until it finishes.'

const unpassed: Record<string, string> = { not_run: 'not run', failed: 'failed', needs_rerun: 'needs a rerun' }

export function startNotice(check: Check): string | null {
  const state = check && unpassed[check.state]
  return state ? `Classroom judgment for the current model has not passed the reference set (${state}). You can still start; see Settings.` : null
}
