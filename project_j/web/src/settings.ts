// The settings page's pure parts: labels and rows for the waiting times and model bindings (ADR-0025).
type Body = Record<string, any>

export const deadlineLabels: Record<string, string> = {
  classroom_judgment: 'Classroom judgment', vocabulary: 'Word meanings', evaluation: 'Evaluation', planning: 'Planning',
  preparation_drafting: 'Preparation: drafting', preparation_image: 'Preparation: pictures', preparation_animation: 'Preparation: animations',
}

export const PURPOSES = ['planning', 'preparation', 'classroom_judgment', 'evaluation', 'vocabulary'] as const
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

export function probeNotes(models: Body | undefined, changedClassroom: boolean): string[] {
  const notes: string[] = []
  if (!models?.initialized) notes.push('This setup has not passed the probe; classes, preparation, evaluation and planning are refused until it does.')
  if (changedClassroom) notes.push('Classroom judgment changed: rerun the reference question set before relying on it.')
  return notes
}
