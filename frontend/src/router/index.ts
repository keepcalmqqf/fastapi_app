import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { title: '登录' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('../views/RegisterView.vue'),
    meta: { title: '注册' },
  },
  {
    // 所有后台页面共用 AdminLayout，子路由的 meta.title/icon 同时用于侧边栏与面包屑
    path: '/',
    component: () => import('../layouts/AdminLayout.vue'),
    redirect: { name: 'dashboard' },
    meta: { requiresAuth: true },
    children: [
      {
        path: '/dashboard',
        name: 'dashboard',
        component: () => import('../views/DashboardView.vue'),
        meta: { title: '概览', icon: 'Odometer', requiresAuth: true },
      },
      {
        path: '/users',
        name: 'users',
        component: () => import('../views/UserManageView.vue'),
        meta: { title: '用户管理', icon: 'User', requiresAuth: true },
      },
      {
        path: '/members',
        name: 'members',
        component: () => import('../views/MemberManageView.vue'),
        meta: { title: '会员管理', icon: 'Avatar', requiresAuth: true },
      },
      {
        path: '/audit-logs',
        name: 'audit-logs',
        component: () => import('../views/AuditLogView.vue'),
        meta: { title: '审计日志', icon: 'Document', requiresAuth: true },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    redirect: { name: 'dashboard' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局前置守卫：先恢复会话，未登录访问受保护页面跳转登录页；已登录访问登录/注册页跳回概览
router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  if (!authStore.restored) {
    await authStore.restore()
  }
  if (to.meta.requiresAuth && !authStore.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (authStore.user && (to.name === 'login' || to.name === 'register')) {
    return { name: 'dashboard' }
  }
  return true
})

export default router
