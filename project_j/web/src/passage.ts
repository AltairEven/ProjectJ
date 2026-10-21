// A reading text as the class shows it: the material's first line is its title (server
// student_text), the rest are paragraphs split by blank lines, numbered as Jane counts them.
export function passage(text: string): { title: string, paragraphs: string[] } {
  const blocks = text.split(/\n\s*\n/).map(block => block.trim()).filter(Boolean)
  const titled = blocks.length > 1 && !blocks[0].includes('\n')
  return { title: titled ? blocks[0] : '', paragraphs: titled ? blocks.slice(1) : blocks }
}
