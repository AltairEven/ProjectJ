import { useState } from 'react'
import { emptyAnswer, fillParts, fillWord, ready, setBlank, tapCard, tapPair, toResponse, type Answer, type TaskView } from './tasks'

// A clicked task in the exercise book (ADR-0020): pick, order, pair or fill, then hand it in.
// There is no text box: these tasks are judged by their own rule and Jane is not asked.
export function TaskInput({ task, disabled, onSubmit, onHint }: {
  task: TaskView, disabled: boolean, onSubmit: (response: Record<string, unknown>) => void, onHint: () => void,
}) {
  const [answer, setAnswer] = useState<Answer>(() => emptyAnswer(task))
  const current = answer.type === task.type ? answer : emptyAnswer(task)
  return <section className="task-input">
    <h2>{{ choice: '选一项', order: '排顺序', match: '连一连', fill: '填一填' }[task.type]}</h2>
    {body(task, current, setAnswer, disabled)}
    <div className="row">
      <button disabled={disabled || !ready(task, current)} onClick={() => onSubmit(toResponse(current))}>提交</button>
      <button className="quiet" disabled={disabled} onClick={onHint}>给我提示</button>
    </div>
  </section>
}

function body(task: TaskView, answer: Answer, set: (answer: Answer) => void, disabled: boolean) {
  switch (task.type) {
    case 'choice': {
      const picked = answer.type === 'choice' ? answer.choice : null
      return <div className="choices" role="group" aria-label="选项">{task.options.map((option, index) =>
        <button key={index} className="quiet" aria-pressed={picked === index} disabled={disabled}
          onClick={() => set({ type: 'choice', choice: index })}>{option}</button>)}</div>
    }
    case 'order': {
      if (answer.type !== 'order') return null
      return <>
        <ol className="placed" aria-label="已排好的顺序">{answer.order.map(index =>
          <li key={index}><button className="quiet" disabled={disabled} onClick={() => set(tapCard(answer, index))}
            aria-label={`撤回：${task.items[index]}`}>{task.items[index]}</button></li>)}</ol>
        <div className="cards" role="group" aria-label="待排的卡片">{task.items.map((item, index) => answer.order.includes(index) ? null :
          <button key={index} className="quiet" disabled={disabled} onClick={() => set(tapCard(answer, index))}>{item}</button>)}</div>
        <p className="hint">按顺序点卡片；点已排好的卡片可以撤回。</p>
      </>
    }
    case 'match': {
      if (answer.type !== 'match') return null
      return <>
        <div className="pairs">
          <div role="group" aria-label="左列">{task.left.map((item, index) => {
            const pair = answer.pairs[index]
            return <button key={index} className="quiet" aria-pressed={answer.picked === index} disabled={disabled}
              onClick={() => set(tapPair(answer, 'left', index))}>{item}{pair !== null && <span className="paired"> = {task.right[pair]}</span>}</button>
          })}</div>
          <div role="group" aria-label="右列">{task.right.map((item, index) =>
            <button key={index} className="quiet" disabled={disabled || answer.picked === null}
              aria-pressed={answer.pairs.includes(index)} onClick={() => set(tapPair(answer, 'right', index))}>{item}</button>)}</div>
        </div>
        <p className="hint">先点左边一项，再点右边和它配的一项；点已配好的左项可以重配。</p>
      </>
    }
    case 'fill': {
      if (answer.type !== 'fill') return null
      const parts = fillParts(task.text)
      return <>
        <p className="fill-text">{parts.map((part, index) => <span key={index}>{part}
          {index < parts.length - 1 && (task.variant === 'free'
            ? <input className="blank" aria-label={`第${index + 1}空`} value={answer.blanks[index]} disabled={disabled}
              onChange={event => set(setBlank(answer, index, event.target.value))} />
            : <button className="quiet blank" aria-label={`第${index + 1}空${answer.blanks[index] ? `：${answer.blanks[index]}，点一下清空` : ''}`}
              disabled={disabled || !answer.blanks[index]} onClick={() => set(setBlank(answer, index, ''))}>{answer.blanks[index] || ' '}</button>)}</span>)}</p>
        {task.bank && <div className="cards" role="group" aria-label="词库">{task.bank.map((word, index) =>
          <button key={index} className="quiet" disabled={disabled} onClick={() => set(fillWord(answer, word))}>{word}</button>)}</div>}
      </>
    }
    default: { const never: never = task; return never }
  }
}
