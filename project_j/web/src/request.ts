const chinese = /[一-鿿]/

/** A rejected request is final; only an unknown outcome may resume the same request identity. */
export type RequestFailure = Error & { unknown: boolean; detail?: string }

export function requestFailure(status: number | undefined, detail?: string): RequestFailure {
  const unknown = status === undefined || status >= 500
  const message = detail && chinese.test(detail) ? detail
    : unknown ? '结果未取得：请接续原操作，不要换新身份重来。'
    : status === 403 || status === 404 ? '链接已失效或对象不存在；请从家长工作台重新打开。'
    : '当前状态不接受这次操作，已保持原状态；请按页面当前内容继续。'
  return Object.assign(new Error(message), { unknown, detail })
}
