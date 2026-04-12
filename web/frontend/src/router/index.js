import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/activities', name: 'Activities', component: () => import('../views/Activities.vue') },
  { path: '/worklogs', name: 'Worklogs', component: () => import('../views/Worklogs.vue') },
  { path: '/issues', name: 'Issues', component: () => import('../views/Issues.vue') },
  { path: '/settings', name: 'Settings', component: () => import('../views/Settings.vue') },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
