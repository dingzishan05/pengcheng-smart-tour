import { expect, test } from '@playwright/test'
import { installBaseMocks, installChatQueue, makePlansChatResponse } from './helpers'

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', '详细流程仅在 Chromium 执行')
  await installBaseMocks(page)
})

test('降级 UI：地图加载失败显示占位提示，行程信息不受影响', async ({ page }) => {
  await installChatQueue(page, [makePlansChatResponse()])

  await page.goto('/')

  const composer = page.locator('textarea[placeholder*="描述您的徐州之旅"]')
  await composer.fill('预算300元一天历史文化')
  await page.locator('.send-btn').click()

  await expect(page.locator('.plan-card')).toHaveCount(3)

  const fallback = page.locator('.map-fallback')
  await expect(fallback).toBeVisible()
  await expect(fallback).toContainText('地图加载失败，行程信息不受影响')
  await expect(fallback).toContainText('完整行程请查看下方时间轴')
})

test('降级 UI：chat 服务 500 后显示错误条与重发按钮，重发后恢复', async ({ page }) => {
  await installBaseMocks(page)
  await installChatQueue(page, [
    { status: 500, body: { code: 50005, message: '系统内部错误，请稍后重试' } },
    {
      conversation_id: '1',
      response_type: 'fallback',
      content: '好的，已收到您的需求。',
      plans: null,
      plan_details_available: false,
      sources: null,
      data_time: null,
      code: null,
      message: null,
    },
  ])

  await page.goto('/')

  const composer = page.locator('textarea[placeholder*="描述您的徐州之旅"]')
  await composer.fill('帮我规划徐州一日游')
  await page.locator('.send-btn').click()

  const errorBar = page.locator('.error-bar')
  await expect(errorBar).toBeVisible()
  await expect(errorBar).toContainText('服务繁忙，请重试')
  await expect(errorBar.locator('.resend-btn')).toBeVisible()
  await expect(errorBar.locator('.resend-btn')).toHaveText('重发')
  await expect(composer).toHaveValue('帮我规划徐州一日游')

  await errorBar.locator('.resend-btn').click()

  await expect(errorBar).toBeHidden({ timeout: 10_000 })
  await expect(page.getByText('好的，已收到您的需求。')).toBeVisible()
})