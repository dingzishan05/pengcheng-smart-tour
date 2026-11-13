<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearAdminSession, getAdminUsername } from '@/api/admin'

const router = useRouter()
const route = useRoute()

const username = computed(() => getAdminUsername() ?? '管理员')

const navs = [
  { path: '/admin', label: '数据源看板' },
  { path: '/admin/pois', label: 'POI 管理' },
  { path: '/admin/prices', label: '价格审核' },
  { path: '/admin/knowledge', label: '知识文档' },
  { path: '/admin/audits', label: '审计日志' },
]

function isActive(path: string): boolean {
  if (path === '/admin') return route.path === '/admin'
  return route.path.startsWith(path)
}

function logout(): void {
  clearAdminSession()
  void router.replace('/admin/login')
}
</script>

<template>
  <el-container class="admin-layout">
    <el-aside width="220px" class="admin-aside">
      <div class="aside-brand">
        <h2 class="brand-name">彭城智游</h2>
        <span class="brand-scope">管理端</span>
      </div>
      <nav class="aside-nav">
        <router-link
          v-for="nav in navs"
          :key="nav.path"
          :to="nav.path"
          class="nav-item"
          :class="{ 'is-active': isActive(nav.path) }"
        >
          {{ nav.label }}
        </router-link>
      </nav>
    </el-aside>
    <el-container>
      <el-header class="admin-header">
        <span class="header-title">{{ navs.find((n) => isActive(n.path))?.label ?? '管理工作台' }}</span>
        <div class="header-right">
          <el-tag size="small" effect="plain">{{ username }}</el-tag>
          <el-button size="small" text type="danger" @click="logout">退出登录</el-button>
        </div>
      </el-header>
      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.admin-layout {
  height: 100%;
}

.admin-aside {
  display: flex;
  flex-direction: column;
  background: #1d2b36;
}

.aside-brand {
  padding: 20px 16px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.brand-name {
  margin: 0;
  font-size: 18px;
  color: #fff;
}

.brand-scope {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.55);
}

.aside-nav {
  flex: 1;
  padding: 12px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  padding: 10px 12px;
  border-radius: 6px;
  color: rgba(255, 255, 255, 0.75);
  text-decoration: none;
  font-size: 14px;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}

.nav-item.is-active {
  background: var(--el-color-primary);
  color: #fff;
}

.admin-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid var(--el-border-color-light);
}

.header-title {
  font-size: 15px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.admin-main {
  background: var(--el-fill-color-lighter);
  overflow-y: auto;
}
</style>