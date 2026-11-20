import { requestFailure } from './request'

export const uid = () => crypto.randomUUID()
export const show = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)

export async function api<T>(token: string, path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
    })
  } catch (error) { throw requestFailure(undefined, (error as Error).message) }
  const result = await response.json().catch(() => undefined)
  if (!response.ok || result === undefined) throw requestFailure(response.ok ? undefined : response.status,
    result?.message ?? (result?.detail === undefined ? undefined : show(result.detail)))
  return result as T
}

// A generated file fetched with the bearer token, as a URL the page can play or show.
export async function fileUrl(token: string, path: string): Promise<string> {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
  if (!response.ok) throw requestFailure(response.status, 'The file did not come back.')
  return URL.createObjectURL(await response.blob())
}

// A file fetched with the bearer token and saved through the browser's download.
export async function saveFile(token: string, path: string, name: string): Promise<void> {
  const url = await fileUrl(token, path)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
