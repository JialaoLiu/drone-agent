import { createRouter, createWebHashHistory } from 'vue-router'

// 用 hash 路由：开发服务器和任意静态托管都不需要额外配置
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'overview', component: () => import('./views/Overview.vue'), meta: { title: '总览' } },
    { path: '/missions', name: 'missions', component: () => import('./views/Missions.vue'), meta: { title: '巡检任务' } },
    { path: '/missions/:id', name: 'mission', component: () => import('./views/MissionDetail.vue'), meta: { title: '任务详情' } },
    { path: '/troubleshoot', name: 'troubleshoot', component: () => import('./views/Troubleshoot.vue'), meta: { title: '飞行排障' } },
    { path: '/scenes', name: 'scenes', component: () => import('./views/Scenes.vue'), meta: { title: '场景库' } },
    { path: '/usage', name: 'usage', component: () => import('./views/Usage.vue'), meta: { title: '用量与成本' } },
    { path: '/evals', name: 'evals', component: () => import('./views/Evals.vue'), meta: { title: '评测结果' } },
    { path: '/help', name: 'help', component: () => import('./views/Help.vue'), meta: { title: '使用说明' } },
  ],
})
