import { expect, test } from '@playwright/test'
import { installBaseMocks, installChatQueue, makePlansChatResponse } from './helpers'

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', '详细流程仅在 Chromium 执行')
  await installBaseMocks(page)
})

test('澄清流程：模糊输入 → 追问卡片 → 回答 → 生成三方案', async ({ page }) => {
  await installChatQueue(page, [
    {
      conversation_id: '1',
      response_type: 'clarify',
      content: '请问您的预算大概是多少？',
      plans: null,
      plan_details_available: false,
      sources: null,
      data_time: null,
      code: null,
      message: null,
    },
    makePlansChatResponse(),
  ])

  await page.goto('/')

  const composer = page.locator('textarea[placeholder*="描述您的徐州之旅"]')
  await composer.fill('想去徐州玩')
  await page.locator('.send-btn').click()

  const clarifyCard = page.locator('.clarify-card')
  await expect(clarifyCard).toBeVisible()
  await expect(clarifyCard).toContainText('第 1 轮追问')
  await expect(clarifyCard).toContainText('请问您的预算大概是多少？')
  await expect(clarifyCard).toContainText('回答后即可继续生成方案')

  await clarifyCard.locator('input').fill('预算300元')
  await clarifyCard.getByRole('button', { name: '提交回答' }).click()

  await expect(page.locator('.plan-card')).toHaveCount(3)
  await expect(clarifyCard).toBeHidden()
  await expect(page.locator('.plan-card').nth(0)).toContainText('省钱')
})