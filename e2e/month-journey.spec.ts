import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const process: any

const BASE = process.env.E2E_BASE || 'https://0.0.0.0:10443'
const USER = process.env.E2E_USER || 'mbaily'
const PASS = process.env.E2E_PASS || 'mypass'

// Login via html_no_js form
async function loginNoJsUI(page: Page) {
  await page.goto(`${BASE}/html_no_js/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input[name="username"]').fill(USER)
  await page.locator('input[name="password"]').fill(PASS)
  await Promise.all([
    page.waitForNavigation(),
    page.locator('form button[type="submit"], button[type="submit"], input[type="submit"]').first().click(),
  ])
  expect(page.url()).toContain('/html_no_js/')
}

async function createList(page: Page, name: string) {
  const res = await page.request.post(`${BASE}/lists?name=${encodeURIComponent(name)}`)
  expect(res.ok()).toBeTruthy()
  const data = await res.json()
  return data.id as number
}

async function createTodo(page: Page, listId: number, text: string) {
  const res = await page.request.post(`${BASE}/todos`, { data: { text, list_id: listId } })
  expect(res.ok()).toBeTruthy()
  const data = await res.json()
  return data.id as number
}

async function getOccurrences(page: Page, params: Record<string, string | number | boolean> = {}) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) qs.set(k, String(v))
  const res = await page.request.get(`${BASE}/calendar/occurrences?${qs.toString()}`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as any
}

async function completeOccurrence(page: Page, hash: string) {
  const res = await page.request.post(`${BASE}/occurrence/complete`, { form: { hash } })
  expect(res.ok()).toBeTruthy()
}

async function uncompleteOccurrence(page: Page, hash: string) {
  const res = await page.request.post(`${BASE}/occurrence/uncomplete`, { form: { hash } })
  expect(res.ok()).toBeTruthy()
}

async function ignoreTodoFrom(page: Page, todoId: number, fromISO: string) {
  const res = await page.request.post(`${BASE}/ignore/scope`, { form: { scope_type: 'todo_from', scope_key: String(todoId), from_dt: fromISO } })
  expect(res.ok()).toBeTruthy()
}

test.describe('Month-long user journey (html_no_js)', () => {
  test('30-day usage flow', async ({ page }) => {
    await loginNoJsUI(page)

    const listName = `Month Journey ${Date.now()}`
    const listId = await createList(page, listName)

    const now = new Date()
    const todos: number[] = []
    for (let d = 1; d <= 30; d++) {
      const dt = new Date(now)
      dt.setUTCDate(dt.getUTCDate() + d)
      const isoDate = dt.toISOString().slice(0, 10)
      const text = `MJ Day ${d} ${isoDate}`
      const tid = await createTodo(page, listId, text)
      todos.push(tid)
    }

    // Ensure UI loads post-login
    await page.goto(`${BASE}/html_no_js/`, { waitUntil: 'networkidle' })
    await page.waitForSelector('body')

    const start = new Date(); start.setUTCDate(start.getUTCDate() - 1)
    const end = new Date(); end.setUTCDate(end.getUTCDate() + 35)
    const occ = await getOccurrences(page, { start: start.toISOString(), end: end.toISOString() })
    const occIds = new Set(occ.occurrences.filter((o: any) => o.item_type === 'todo').map((o: any) => o.id))
    const seenCount = todos.filter(t => occIds.has(t)).length
    expect(seenCount).toBeGreaterThan(20)

    const firstOcc = occ.occurrences.find((o: any) => o.item_type === 'todo' && o.id === todos[0])
    if (firstOcc) {
      await completeOccurrence(page, firstOcc.occ_hash)
      const occ2 = await getOccurrences(page, { start: start.toISOString(), end: end.toISOString() })
      const again = occ2.occurrences.find((o: any) => o.occ_hash === firstOcc.occ_hash)
      expect(again?.completed).toBeTruthy()
      await uncompleteOccurrence(page, firstOcc.occ_hash)
    }

    const mid = new Date(now); mid.setUTCDate(mid.getUTCDate() + 15)
    await ignoreTodoFrom(page, todos[todos.length - 1], mid.toISOString())
    const occ3 = await getOccurrences(page, { start: start.toISOString(), end: end.toISOString(), include_ignored: true })
    const maybeIgnored = occ3.occurrences.find((o: any) => o.item_type === 'todo' && o.id === todos[todos.length - 1])
    expect(maybeIgnored).toBeTruthy()
  })
})
