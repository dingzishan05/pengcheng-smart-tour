<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { adminLogin } from '@/api/admin'

const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const loading = ref(false)

async function submit(): Promise<void> {
  if (!username.value.trim() || !password.value) {
    ElMessage.warning('请输入用户名与密码')
    return
  }
  loading.value = true
  try {
    await adminLogin(username.value.trim(), password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/admin'
    await router.replace(redirect)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '登录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="admin-login-page">
    <el-card class="login-card">
      <template #header>
        <div class="card-head">
          <h2 class="login-title">彭城智游 · 管理端</h2>
          <p class="login-sub">数据治理与运营工作台</p>
        </div>
      </template>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="username" placeholder="请输入管理员用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            placeholder="请输入密码"
            show-password
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button class="login-btn" type="primary" native-type="submit" :loading="loading">
          登 录
        </el-button>
      </el-form>
      <p class="login-hint">连续失败 5 次将锁定账号 10 分钟</p>
    </el-card>
  </main>
</template>

<style scoped>
.admin-login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--el-fill-color-lighter);
}

.login-card {
  width: 380px;
}

.card-head {
  text-align: center;
}

.login-title {
  margin: 0;
  font-size: 18px;
}

.login-sub {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.login-btn {
  width: 100%;
}

.login-hint {
  margin: 12px 0 0;
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>