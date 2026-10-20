// Onboarding checks that mirror the server's, so the student hears about a slip before sending.

/** null when the server would accept the Lexile measure ("650L" or "BR100L"). */
export function lexileError(raw: string): string | null {
  const value = raw.replace(/\s+/g, '').toUpperCase()
  if (!value) return '请填写蓝思值。'
  if (/^(BR)?\d{1,4}L$/.test(value)) return null
  if (/^(BR)?\d+$/.test(value)) return '蓝思值以L结尾，如650L。'
  if (/^(BR)?\d{5,}L$/.test(value)) return '数字最多4位，如1200L。'
  return '蓝思值写成“数字+L”（如650L）或“BR+数字+L”（如BR100L）。'
}

export const chatComplete = (answers: string[], count: number): boolean =>
  count > 0 && answers.length === count && answers.every(answer => answer.trim() !== '')
