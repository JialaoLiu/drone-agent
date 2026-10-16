import { createRouter, createWebHashHistory } from 'vue-router'

// hash 路由：开发服务器和任意静态托管都不需要额外配置
// 工作台：左侧新任务与历史，右侧是欢迎页或某个任务（巡检与飞控诊断共用任务视图）
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/', component: () => import('./views/Workbench.vue'), meta: { title: '工作台', flush: true },
      children: [
        { path: '', name: 'home', component: () => import('./views/Home.vue'), meta: { title: '工作台', flush: true } },
        { path: 'tasks/:id', name: 'task', component: () => import('./views/TaskView.vue'), meta: { title: '工作台', flush: true } },
      ],
    },
    { path: '/missions/:id', redirect: (to) => `/tasks/${to.params.id}` },
    { path: '/missions', redirect: '/' },
    { path: '/troubleshoot', redirect: '/diagnostics' },
    { path: '/diagnostics', name: 'diagnostics', component: () => import('./views/Diagnostics.vue'), meta: { title: '飞控诊断' } },
    { path: '/scenes', name: 'scenes', component: () => import('./views/Scenes.vue'), meta: { title: '场景库' } },
    { path: '/usage', name: 'usage', component: () => import('./views/Usage.vue'), meta: { title: '用量与成本' } },
    { path: '/evals', name: 'evals', component: () => import('./views/Evals.vue'), meta: { title: '评测结果' } },
    { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '设置' } },
    { path: '/help', name: 'help', component: () => import('./views/Help.vue'), meta: { title: '使用说明' } },
  ],
})
