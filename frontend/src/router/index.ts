import { createRouter, createWebHistory } from 'vue-router'
import { getAdminToken } from '@/api/admin'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'tourist-home',
      component: () => import('@/views/tourist/ChatHomeView.vue'),
    },
    {
      path: '/admin/login',
      name: 'admin-login',
      component: () => import('@/views/admin/AdminLoginView.vue'),
    },
    {
      path: '/admin',
      component: () => import('@/views/admin/AdminLayoutView.vue'),
      children: [
        {
          path: '',
          name: 'admin-dashboard',
          component: () => import('@/views/admin/AdminDashboardView.vue'),
        },
        {
          path: 'pois',
          name: 'admin-pois',
          component: () => import('@/views/admin/AdminPoisView.vue'),
        },
        {
          path: 'prices',
          name: 'admin-prices',
          component: () => import('@/views/admin/AdminPricesView.vue'),
        },
        {
          path: 'knowledge',
          name: 'admin-knowledge',
          component: () => import('@/views/admin/AdminKnowledgeView.vue'),
        },
        {
          path: 'audits',
          name: 'admin-audits',
          component: () => import('@/views/admin/AdminAuditsView.vue'),
        },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (to.path.startsWith('/admin') && to.path !== '/admin/login') {
    if (!getAdminToken()) {
      return { name: 'admin-login', query: { redirect: to.fullPath } }
    }
  }
  if (to.path === '/admin/login' && getAdminToken()) {
    return { name: 'admin-dashboard' }
  }
  return true
})

export default router