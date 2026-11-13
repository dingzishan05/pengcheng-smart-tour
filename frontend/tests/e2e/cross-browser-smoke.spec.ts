import { expect, test } from '@playwright/test'
import { installBaseMocks, installChatQueue, makePlansChatResponse } from './helpers'

test('三浏览器冒烟：首屏渲染与演示主流程输入', async ({ page }) => {
  await installBaseMocks(page)
  await installChatQueue(page, [makePlansChatResponse()])

  await page.goto('/')

  await expect(page.locator('.brand-title')).toHaveText('彭城智游')
  await expect(page.locator('.brand-sub')).toHaveText('徐州 AI 城市旅游智能体')
  await expect(page.locator('textarea[placeholder*="描述您的徐州之旅"]')).toBeVisible()
  await expect(page.getByText('告诉我您的出行想法', { exact: false })).toBeVisible()

  const composer = page.locator('textarea[placeholder*="描述您的徐州之旅"]')
  await composer.fill('预算300元一天历史文化')
  await page.locator('.send-btn').click()

  await expect(page.locator('.plan-card')).toHaveCount(3, { timeout: 20_000 })
  await expect(page.locator('.plan-card').nth(0)).toContainText('省钱')
})