// A reading text as the class shows it: the material's first line is its title (server
// student_text), the rest are paragraphs split by blank lines, numbered as Jane counts them.
export function passage(text: string): { title: string, paragraphs: string[] } {
  const blocks = text.split(/\n\s*\n/).map(block => block.trim()).filter(Boolean)
  const titled = blocks.length > 1 && !blocks[0].includes('\n')
  return { title: titled ? blocks[0] : '', paragraphs: titled ? blocks.slice(1) : blocks }
}

// Story words the student can tap while reading: letters with an inner apostrophe; everything
// else (spaces, punctuation, line breaks) stays as it is between them.
const WORD = /[A-Za-z]+(?:['’][A-Za-z]+)?/g
export function wordTokens(paragraph: string): { text: string, word: boolean }[] {
  const tokens: { text: string, word: boolean }[] = []
  let last = 0
  for (const match of paragraph.matchAll(WORD)) {
    if (match.index > last) tokens.push({ text: paragraph.slice(last, match.index), word: false })
    tokens.push({ text: match[0], word: true })
    last = match.index + match[0].length
  }
  if (last < paragraph.length) tokens.push({ text: paragraph.slice(last), word: false })
  return tokens
}

/** The sentence (ending in . ! or ?) that contains the character at offset, trimmed. */
export function sentenceOf(paragraph: string, offset: number): string {
  let start = 0
  for (const match of paragraph.matchAll(/[^.!?]*[.!?]+[”"’]?/g)) {
    const end = match.index + match[0].length
    if (offset < end) return match[0].trim()
    start = end
  }
  return paragraph.slice(start).trim()
}
