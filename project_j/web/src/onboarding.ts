// Onboarding checks that mirror the server's, so the student hears about a slip before sending.

/** null when the server would accept the Lexile measure ("650L" or "BR100L"). */
export function lexileError(raw: string): string | null {
  const value = raw.replace(/\s+/g, '').toUpperCase()
  if (!value) return 'Please enter your Lexile.'
  if (/^(BR)?\d{1,4}L$/.test(value)) return null
  if (/^(BR)?\d+$/.test(value)) return 'A Lexile ends with L, like 650L.'
  if (/^(BR)?\d{5,}L$/.test(value)) return 'Use at most 4 digits, like 1200L.'
  return 'Write a number and L (like 650L) or BR, a number and L (like BR100L).'
}

export const chatComplete = (answers: string[], count: number): boolean =>
  count > 0 && answers.length === count && answers.every(answer => answer.trim() !== '')
