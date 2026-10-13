<script setup lang="ts">
// 网页内的简要使用说明；完整的图文手册见仓库 docs/user-guide.md
const STEPS = [
  { title: '1. 启动仿真', img: 'help/scenes.png',
    text: '左侧进入“场景库”。每个场景由随机种子生成（围栏、禁飞区、阵列数量位置朝向、树、缺陷都不同）。点“启动仿真”，约 30 秒后左下角显示“仿真运行中”。也可以输入种子或点“随机生成一个”创建新场景。' },
  { title: '2. 下达巡检任务', img: 'help/missions.png',
    text: '进入“巡检任务”，用一句话描述任务，或点示例。说“快速看一下”只看整列照片；说“一块都不要漏、重点看裂纹”会逐块近距复核，电量不够时自动分架次。越权的要求（飞进禁飞区、关闭限高）会被拒绝。' },
  { title: '3. 确认计划', img: 'help/plan.png',
    text: '下达后自动进入任务详情。调度员理解任务、规划员给出航线与复核策略、安全员校验围栏、禁飞区、障碍物、高度与电量。右侧“计划待确认”卡片里点“确认起飞”。地图上虚线是航线，空心圆是绕开禁飞区的绕行点。' },
  { title: '4. 观察执行', img: 'help/flight.png',
    text: '地图上蓝色三角是无人机实时位置；右侧“团队协作记录”按角色列出每一步；照片实时出现在下方。顶部可看电量、高度、飞行模式，紧急情况点右上角“一键返航”。' },
  { title: '5. 查看结果与报告', img: 'help/findings.png',
    text: '“巡检发现”卡片显示每块板的正视图、结论、类型、置信度与依据，可按状态和类型筛选；勾选地图上方“显示标准答案”可对照检出是否准确。点右上角“查看报告”打开 HTML 报告。' },
  { title: '6. 飞行排障', img: 'help/troubleshoot.png',
    text: '进入“飞行排障”，描述问题（例如转向慢），演示时可以先让系统悄悄注入一个故障。诊断员会试飞、对比参数、做对照实验，并给出结论与修改建议。' },
  { title: '7. 用量与评测', img: 'help/usage.png',
    text: '“用量与成本”按角色、模型、日期、任务列出 Token、图片、耗时和费用；“评测结果”展示巡检、规划、排障、随机布局四类评测的最新成绩和明细。' },
]
</script>

<template>
  <div class="page-head"><p>完整的图文手册见仓库 <code>docs/user-guide.md</code>；以下是网页里的快速说明。</p></div>
  <div class="steps">
    <section v-for="s in STEPS" :key="s.title" class="card step">
      <div>
        <h2>{{ s.title }}</h2>
        <p>{{ s.text }}</p>
      </div>
      <img :src="s.img" :alt="s.title" loading="lazy" @error="($event.target as HTMLImageElement).style.display = 'none'" />
    </section>
  </div>
  <section class="card faq">
    <h2>常见问题</h2>
    <dl>
      <dt>仿真没有反应 / 一直“启动中”？</dt><dd>确认外置硬盘上的 DevData 映像已挂载；在终端运行 <code>scripts/start.sh stop</code> 后再 <code>scripts/start.sh</code>。日志在 /Volumes/DevData/drone-agent/logs。</dd>
      <dt>任务卡在“等待确认”？</dt><dd>打开任务详情，在右侧计划卡片点“确认起飞”；或下达任务时勾选“自动起飞”。</dd>
      <dt>想换一个完全不同的场地？</dt><dd>场景库里输入新的种子或点“随机生成一个”，再启动仿真。</dd>
      <dt>飞机悬停不动？</dt><dd>点右上角“一键返航”。任务出错时系统也会自动返航，原因写在协作记录里。</dd>
    </dl>
  </section>
</template>

<style scoped>
.steps { display: flex; flex-direction: column; gap: 12px; }
.step { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(0, 1.4fr); gap: 16px; align-items: start; }
.step p { line-height: 1.8; font-size: 14px; margin: 0; }
.step img { width: 100%; border-radius: 8px; border: 1px solid var(--line); }
.faq { margin-top: 14px; }
dt { font-weight: 600; margin-top: 10px; }
dd { margin: 4px 0 0; color: var(--muted); font-size: 14px; line-height: 1.7; }
code { background: var(--code); padding: 1px 5px; border-radius: 4px; font-size: 13px; }
@media (max-width: 860px) { .step { grid-template-columns: 1fr; } }
</style>
