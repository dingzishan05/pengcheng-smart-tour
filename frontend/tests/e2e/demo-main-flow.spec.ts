import { expect, test } from '@playwright/test'
import {
  installBaseMocks,
  installChatQueue,
  makePlanDetail,
  makePlansChatResponse,
  makeRefreshResponse,
} from './helpers'

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', '详细流程仅在 Chromium 执行')
  await installBaseMocks(page)
  await installChatQueue(page, [makePlansChatResponse()])
})

test('演示主流程：输入需求 → 三方案 → 切换对比 → 预算改150 → diff → 确认', async ({ page }) => {
  await page.route('**/api/v1/plans/*/refresh', (route) =>
    route.fulfill({ json: makeRefreshResponse() }),
  )
  const confirmRequests: string[] = []
  await page.route('**/api/v1/plans/*/confirm*', async (route) => {
    confirmRequests.push(route.request().url())
    await route.fulfill({
      json: {
        plan_id: '6',
        status: 'confirmed',
        plan_detail: makePlanDetail('6', '省钱', '148.00'),
      },
    })
  })

  await page.goto('/')

  await expect(page.getByText('告诉我您的出行想法', { exact: false })).toBeVisible()

  const composer = page.locator('textarea[placeholder*="描述您的徐州之旅"]')
  await composer.fill('预算300元一天历史文化')
  await page.locator('.send-btn').click()

  const cards = page.locator('.plan-card')
  await expect(cards).toHaveCount(3)
  await expect(cards.nth(0)).toContainText('省钱')
  await expect(cards.nth(1)).toContainText('省时')
  await expect(cards.nth(2)).toContainText('均衡')
  await expect(cards.nth(0)).toHaveClass(/is-selected/)
  await expect(page.locator('.data-time-bar')).toContainText('2026年10月20日')

  const detailRequest = page.waitForRequest((request) =>
    /\/api\/v1\/plans\/2(\?|$)/.test(request.url()),
  )
  await cards.nth(1).click()
  await detailRequest
  await expect(cards.nth(1)).toHaveClass(/is-selected/)
  await expect(cards.nth(0)).not.toHaveClass(/is-selected/)

  await cards.nth(0).click()
  await page.locator('.budget-adjust input').fill('150')
  await page.getByRole('button', { name: '重新规划' }).click()

  const dialog = page.locator('.el-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('方案调整对比')
  await expect(dialog.locator('.diff-summary')).toContainText('预算由300调整为150')
  await expect(dialog.locator('.diff-summary')).toContainText('费用变化 -¥40.00')
  await expect(dialog.locator('.diff-col').first()).toContainText('当前方案')
  await expect(dialog.locator('.diff-col').last()).toContainText('调整后方案')
  await expect(dialog.locator('.diff-item.is-removed')).toContainText('龟山汉墓')
  await expect(dialog.locator('.diff-item.is-added')).toContainText('汉文化景区')

  await page.getByRole('button', { name: '确认调整' }).click()
  await expect
    .poll(() => confirmRequests.length, { timeout: 10_000 })
    .toBeGreaterThanOrEqual(1)
  expect(confirmRequests[0]).toContain('/api/v1/plans/6/confirm')
  await expect(dialog).toBeHidden({ timeout: 10_000 })
})