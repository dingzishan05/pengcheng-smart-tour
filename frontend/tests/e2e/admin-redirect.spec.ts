import { expect, test } from '@playwright/test'

test('管理端未登录访问 /admin 重定向到登录页并携带 redirect 参数', async ({ page }) => {
  await page.goto('/admin')

  await page.waitForURL((url) => {
    return url.pathname === '/admin/login' && url.searchParams.get('redirect') === '/admin'
  })
  await expect(page.locator('.el-form')).toBeVisible()
  await expect(page.getByPlaceholder('请输入管理员用户名')).toBeVisible()
  await expect(page.getByPlaceholder('请输入密码')).toBeVisible()

  const adminContent = page.locator('.brand-title')
  await expect(adminContent).toHaveCount(0)
})

test('管理端未登录访问子页面同样重定向', async ({ page }) => {
  await page.goto('/admin/knowledge')

  await page.waitForURL((url) => {
    return (
      url.pathname === '/admin/login' &&
      url.searchParams.get('redirect') === '/admin/knowledge'
    )
  })
})