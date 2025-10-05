import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare const process: any

const BASE = process.env.E2E_BASE || 'https://0.0.0.0:10443'
const USER = process.env.E2E_USER || 'mbaily'
const PASS = process.env.E2E_PASS || 'mypass'

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

async function completeOccurrence(page: Page, occ: any) {
  const form: Record<string,string> = { hash: occ.occ_hash }
  if (occ.item_type) form.item_type = occ.item_type
  if (occ.id != null) form.item_id = String(occ.id)
  if (occ.occurrence_dt) form.occurrence_dt = occ.occurrence_dt
  const res = await page.request.post(`${BASE}/occurrence/complete`, { form })
  expect(res.ok()).toBeTruthy()
}

test.describe('Daily recurrence retention after rule change', () => {
  test('completed daily occurrences remain visible as historic after rule change', async ({ page }) => {
    await loginNoJsUI(page)

    const listId = await createList(page, 'Daily Retention ' + Date.now())

    const today = new Date()
    const isoDate = today.toISOString().slice(0,10)
    const text = `Daily Retention Event ${isoDate} every day`
    const todoId = await createTodo(page, listId, text)

    const start = new Date(); start.setUTCDate(start.getUTCDate() - 1)
    const end = new Date(); end.setUTCDate(end.getUTCDate() + 10)
    let occResp = await getOccurrences(page, { start: start.toISOString(), end: end.toISOString() })
    const ourOccs = occResp.occurrences.filter((o: any) => o.item_type === 'todo' && o.id === todoId)
    expect(ourOccs.length).toBeGreaterThanOrEqual(5)

    const toComplete = ourOccs.slice(0,3)
    for (const occ of toComplete) {
      await completeOccurrence(page, occ)
    }

    occResp = await getOccurrences(page, { start: start.toISOString(), end: end.toISOString() })
    const afterComplete = occResp.occurrences.filter((o: any) => o.item_type === 'todo' && o.id === todoId)
    for (const occ of toComplete) {
      const match = afterComplete.find((o: any) => o.occurrence_dt === occ.occurrence_dt)
      expect(match?.completed).toBeTruthy()
    }

    // Change to single plain date 30 days out
    const singleDate = new Date(); singleDate.setUTCDate(singleDate.getUTCDate() + 30)
    const singleIso = singleDate.toISOString().slice(0,10)
    const newText = `One-off Replacement ${singleIso}`
    const patchRes = await page.request.patch(`${BASE}/todos/${todoId}`, { data: { text: newText } })
    expect(patchRes.ok()).toBeTruthy()

    const noHistoric = await getOccurrences(page, { start: start.toISOString(), end: end.toISOString() })
    const stillGenerated = noHistoric.occurrences.filter((o: any) => o.item_type==='todo' && o.id===todoId)
  const oldDailyFound = toComplete.some((orig: any) => stillGenerated.find((o: any) => o.occurrence_dt === orig.occurrence_dt))
    expect(oldDailyFound).toBeFalsy()

    const withHistoric = await getOccurrences(page, { start: start.toISOString(), end: end.toISOString(), include_historic: true })
    const historicSet = withHistoric.occurrences.filter((o: any) => o.item_type==='todo' && o.id===todoId)
    for (const occ of toComplete) {
      const match = historicSet.find((o: any) => o.occurrence_dt === occ.occurrence_dt)
      expect(match).toBeTruthy()
      expect(match?.completed).toBeTruthy()
      expect(match?.historic).toBeTruthy()
      expect(match?.phantom).toBeTruthy()
    }
  })
})
