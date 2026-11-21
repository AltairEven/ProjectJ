import { Fragment, useEffect, useState, type CSSProperties } from 'react'
import { api, fileUrl, saveFile } from '../api'
import { bookLine, bookTitle, bookView, changesLine, coverHue, coverOf, draftActions, expandBlocked, missingLine, neighbours, outlineLine, shelfItem, type ShelfMode } from '../books'
import type { BookContent, BookPlace, BookRow } from '../client/types.gen'
import { courseRows } from '../courses'
import { jobProgress } from '../progress'
import { Progress, useRun, type PageProps } from '../ui'

const download = (token: string, book: BookRow) => () =>
  saveFile(token, `/api/books/file?${new URLSearchParams({ book_id: book.book_id })}`, `${book.book_id}.epub`)

/** A book's cover as a shelf mode shows it (ADR-0036): the drawn picture once it has come, else a plain
 * cover in the book's own colour with its title. Hidden from screen readers: the title next to it names the book. */
function Cover({ token, learner, book, mode, small }: Pick<PageProps, 'token' | 'learner'> & { book: BookRow, mode: ShelfMode, small?: boolean }) {
  const shown = coverOf(book, mode)
  const [url, setUrl] = useState<string>()
  const id = book.book_id, draft = shown?.draft, version = shown?.version
  useEffect(() => {
    if (version === undefined) return
    let live = true, made: string | undefined
    const query = new URLSearchParams({ book_id: id, draft: String(draft), cover: 'true', learner_id: learner })
    void fileUrl(token, `/api/books/picture?${query}`).then(next => {
      if (live) { made = next; setUrl(next) } else URL.revokeObjectURL(next)
    }, () => {})  // A cover that does not come back stays plain.
    return () => { live = false; if (made) URL.revokeObjectURL(made); setUrl(undefined) }
  }, [token, learner, id, draft, version])
  return <span className={small ? 'cover small' : 'cover'} style={{ '--hue': coverHue(id) } as CSSProperties} aria-hidden="true">
    {url ? <img src={url} alt="" onError={() => setUrl(undefined)} /> : <span>{bookTitle(book)}</span>}</span>
}

/** The books from taken lessons (application/books.py, ADR-0035): Read opens a saved book or downloads it, Edit opens any book's edit page or deletes it. */
function Shelf({ token, dashboard, learner, go, refresh }: PageProps) {
  const [mode, setMode] = useState<ShelfMode>('read')
  const { busy, run, status } = useRun(refresh)
  const lessons = courseRows(dashboard.objects ?? [])
  const books = dashboard.books ?? []
  return <section><h2>Bookshelf</h2>
    <div className="row">{(['read', 'edit'] as const).map(name => <button key={name} className={mode === name ? '' : 'quiet'}
      aria-pressed={mode === name} onClick={() => setMode(name)}>{name === 'read' ? 'Read' : 'Edit'}</button>)}</div>
    {status}
    {books.length ? <ul className="shelf">{books.map(book => {
      const item = shelfItem(book, mode)
      const lesson = lessons.find(row => row.object_id === book.course_ref.object_id)?.title
      const cover = <><Cover token={token} learner={learner} book={book} mode={mode} /><span className="title">{bookTitle(book)}</span></>
      return <li key={book.book_id}>
        {item.opens ? <button className="shelf-book" onClick={() => go('books', `${item.opens}:${book.book_id}`)}>{cover}</button>
          : <div className="shelf-book greyed">{cover}</div>}
        {item.unsaved && <span className="tag">Unsaved changes</span>}
        <p className={item.opens ? undefined : 'greyed'}>{item.line}</p>
        {lesson && <p className="hint">From: {lesson}</p>}
        {item.opens === 'read' && <button className="quiet" disabled={busy} aria-label={`Download EPUB of ${bookTitle(book)}`}
          onClick={() => void run(download(token, book))}>Download EPUB</button>}
        {mode === 'edit' && <button className="quiet" disabled={busy || !!dashboard.active} aria-label={`Delete ${bookTitle(book)}`} onClick={() => {
          const writing = (dashboard.jobs ?? []).some(job => job.name === `reading:${book.book_id}` && job.state === 'running')
          if (confirm(`${writing ? 'It is being written now; deleting stops that. ' : ''}Delete "${bookTitle(book)}"? Its saved version, draft, pictures and EPUB all go.`)
            && confirm('This cannot be undone. Delete the book for good?'))
            void run(() => api<void>(token, 'books/delete', { learner_id: learner, book_id: book.book_id }), 'Deleting the book')
        }}>Delete</button>}
      </li>
    })}</ul> : <p>No books yet. Expand a lesson you have taken, on its lesson page.</p>}
  </section>
}

const placeKey = (place: BookPlace) => `${place.paragraph}:${place.picture ?? ''}`

/** A book one chapter at a time, as on a phone: only read, no word marking, nothing recorded. The draft is shown the same way. */
function Reader({ token, learner, go, book, draft }: Pick<PageProps, 'token' | 'learner' | 'go'> & { book: BookRow, draft: boolean }) {
  const id = book.book_id
  const [content, setContent] = useState<BookContent>()
  const [error, setError] = useState('')
  const [index, setIndex] = useState(0)
  const [pictures, setPictures] = useState<Record<string, string>>({})
  useEffect(() => {
    api<BookContent>(token, `books/content?${new URLSearchParams({ book_id: id, draft: String(draft) })}`)
      .then(setContent, failure => setError((failure as Error).message))
  }, [token, id, draft])
  const chapter = content?.chapters[index]
  useEffect(() => {
    scrollTo(0, 0)
    if (!chapter) return
    let live = true
    const made: string[] = []
    for (const place of chapter.pictures) {
      const query = new URLSearchParams({ book_id: id, draft: String(draft), learner_id: learner })
      query.set(place.picture === null ? 'chapter' : 'picture', place.picture ?? String(index + 1))
      void fileUrl(token, `/api/books/picture?${query}`).then(url => {
        if (!live) { URL.revokeObjectURL(url); return }
        made.push(url)
        setPictures(current => ({ ...current, [placeKey(place)]: url }))
      }, () => {})  // A picture that does not come back is left out.
    }
    return () => { live = false; made.forEach(url => URL.revokeObjectURL(url)); setPictures({}) }
  }, [token, id, draft, learner, index, chapter])
  const count = content?.chapters.length ?? 0
  const near = neighbours(index, count)
  return <>
    {draft ? <button className="quiet" onClick={() => go('books', `edit:${id}`)}>← Back to editing</button>
      : <button className="quiet" onClick={() => go('books')}>← Bookshelf</button>}
    <section className="reader"><div className="row"><h2>{content?.title ?? bookTitle(book)}</h2>{draft && <span className="tag">Draft</span>}</div>
      {error && <div className="notice" role="alert">{error}</div>}
      {!content && !error && <p>Loading…</p>}
      {content && !count && <p>No chapter is written yet.</p>}
      {chapter && <>
        <select aria-label="Contents" value={index} onChange={event => setIndex(Number(event.target.value))}>
          {content.chapters.map((page, n) => <option key={n} value={n}>Chapter {n + 1} · {page.title}</option>)}</select>
        <article><h3>Chapter {index + 1} · {chapter.title}</h3>
          {chapter.paragraphs.map((text, n) => <Fragment key={n}><p>{text}</p>
            {chapter.pictures.filter(place => place.paragraph === n + 1 && pictures[placeKey(place)])
              .map(place => <img key={placeKey(place)} src={pictures[placeKey(place)]} alt="" />)}</Fragment>)}</article>
        <div className="row">
          <button className="quiet" disabled={near.previous === null} onClick={() => setIndex(near.previous ?? index)}>Previous chapter</button>
          <button className="quiet" disabled={near.next === null} onClick={() => setIndex(near.next ?? index)}>Next chapter</button></div></>}
    </section></>
}

/** Everything done to a book, always on its draft: agree its outline, follow the writing, retry, rewrite a chapter,
 * draw missing pictures, agree new chapters; then save the draft over the book, discard it, or preview it. */
function Editor({ token, dashboard, learner, go, refresh, book }: PageProps & { book: BookRow }) {
  const { busy, run, status } = useRun(refresh)
  const [opened, setOpened] = useState(false)
  const [revising, setRevising] = useState<number | null>(null)
  const [feedback, setFeedback] = useState('')
  const [extending, setExtending] = useState(false)
  const [note, setNote] = useState('')
  const id = book.book_id
  const blocked = expandBlocked(dashboard)
  const stop = busy || !!blocked
  const job = (dashboard.jobs ?? []).find(item => item.name === `reading:${id}` && item.state === 'running')
  const send = (route: string, label: string, extra = {}) => void run(async () => {
    await api(token, `books/${route}`, { learner_id: learner, book_id: id, ...extra })
    setNote('')
    setExtending(false)
  }, label)
  const said = note.trim()
  const noteBox = (label: string) => <label>{label}<textarea maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></label>
  const outline = book.outline
  // The outline still to agree: a new book's whole one, or only the new chapters of a continuation.
  const discussed = !job && outline && (book.state === 'outlining' || book.continuing) ? outline : null
  const first = book.continuing ? book.chapters.length : 0
  const actions = draftActions(book), missing = book.state === 'ready' ? missingLine(book) : null
  return <>
    <button className="quiet" onClick={() => go('books')}>← Bookshelf</button>
    <section><h2>{bookTitle(book)}</h2>
      <div className="row"><Cover token={token} learner={learner} book={book} mode="edit" small />
        {book.state === 'ready' && <button className="quiet" disabled={stop} onClick={() => send('cover', 'Redrawing the cover')}>Redraw the cover</button>}</div>
      {status}
      {actions && <>
        <p>{book.saved ? `Unsaved changes: ${book.changes ? changesLine(book.changes) : 'No changes yet'}` : 'Not saved yet: it goes on the shelf to read once you save it.'}</p>
        <div className="row">
          <button disabled={stop || !!actions.reason} onClick={() => send('save', 'Saving the book')}>Save (replace the book)</button>
          {actions.reason && <span className="hint">{actions.reason}</span>}
          {actions.revert && <button className="quiet" disabled={stop} onClick={() => {
            if (confirm('Discard every change since the book was last saved?')) send('revert', 'Discarding the changes')
          }}>Discard changes</button>}
          <button className="quiet" disabled={!actions.preview} onClick={() => go('books', `draft:${id}`)}>Preview the draft</button>
        </div></>}
      {blocked && <p className="notice">{blocked}</p>}
      {job ? <Progress label={outlineLine(book, true) ?? bookLine(book)} {...jobProgress(job.progress)} />
        : <p className={book.state === 'failed' ? 'notice' : undefined}>{bookLine(book)}</p>}
      {!job && book.continuing && <p>New chapters: {outlineLine(book, false)}</p>}
      {(book.state === 'outlining' || book.state === 'ready') && book.reason && <p className="notice">The last step failed: {book.reason}</p>}
      {discussed && <>
        {discussed.summary && <p>The story: {discussed.summary}</p>}
        <ol start={first + 1}>{discussed.chapters.slice(first).map((chapter, index) => <li key={index}>{chapter.title} — {chapter.plot}</li>)}</ol>
        {noteBox('What should change?')}
        <div className="row">
          <button disabled={stop || !said} onClick={() => send('outline', 'Revising the outline', { feedback: said })}>Revise the outline</button>
          <button disabled={stop} onClick={() => send('confirm', 'Writing the book')}>{book.continuing ? 'Write the new chapters' : 'Write the book from this outline'}</button>
          {book.continuing && <button className="quiet" disabled={stop} onClick={() => send('discard', 'Discarding the new chapters')}>Discard the new chapters</button>}
        </div></>}
      <div className="row">
        {book.state === 'ready' && <button className="quiet" onClick={() => setOpened(!opened)}>{opened ? 'Hide chapters' : 'Chapters'}</button>}
        {missing && <button className="quiet" disabled={stop} onClick={() => send('pictures', 'Drawing missing pictures')}>Draw missing pictures</button>}
        {missing && <span className="hint">{missing}</span>}
        {book.state === 'ready' && !book.continuing && <button className="quiet" disabled={stop} onClick={() => setExtending(!extending)}>Continue the story</button>}
        {book.state === 'failed' && <button disabled={stop} onClick={() => send('retry', 'Retrying the book')}>Retry</button>}
      </div>
      {book.state === 'ready' && !book.continuing && extending && <>
        {noteBox('What should happen next?')}
        <button disabled={stop || !said} onClick={() => send('outline', 'Planning new chapters', { feedback: said })}>Plan new chapters</button></>}
      {book.state === 'ready' && opened && <ol>{book.chapters.map((title, index) => {
        const chapter = index + 1, plot = outline?.chapters[index]?.plot
        return <li key={chapter}>{plot ? `${title} — ${plot}` : title} <button className="quiet" disabled={stop} onClick={() => { setRevising(chapter); setFeedback('') }}>Revise</button>
          {revising === chapter && <><label>What should change in this chapter?<textarea maxLength={2000} value={feedback} onChange={event => setFeedback(event.target.value)} /></label>
            <button disabled={stop || !feedback.trim()} onClick={() => { const body = { learner_id: learner, book_id: id, chapter, feedback: feedback.trim() }
              void run(async () => { await api(token, 'books/revise', body); setRevising(null); setFeedback('') }, 'Rewriting the chapter') }}>Rewrite this chapter</button></>}</li>
      })}</ol>}
      {book.saved && <button className="quiet" disabled={busy} onClick={() => void run(download(token, book))}>Download EPUB</button>}
    </section></>
}

export default function Books(props: PageProps) {
  const view = bookView(props.detail)
  if (!view) return <Shelf {...props} />
  const book = (props.dashboard.books ?? []).find(row => row.book_id === view.book)
  if (!book) return <><button className="quiet" onClick={() => props.go('books')}>← Bookshelf</button><p>This book is not on the shelf.</p></>
  if (view.mode === 'edit') return <Editor {...props} book={book} />
  return <Reader {...props} book={book} draft={view.mode === 'draft'} />
}
