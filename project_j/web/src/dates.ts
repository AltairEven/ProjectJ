/** Dates the way the page shows them: always US English, in the viewer's time zone. */
const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
const moment = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })

export const dateText = (value: string | Date) => day.format(new Date(value))
export const dateTimeText = (value: string | Date) => moment.format(new Date(value))
