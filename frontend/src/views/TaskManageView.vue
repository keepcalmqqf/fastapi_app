<script setup lang="ts">
import { ElMessage } from 'element-plus'
import {
  dispatchTask,
  getSchedulerJobs,
  getTaskStatus,
  PluginDisabledError,
} from '../api/task'
import type { ScheduledJob, SchedulerStatus, TaskJob, TaskState, TaskStatus } from '../api/task'
import { usePermissionStore } from '../stores/permission'
import { formatTime } from '../utils/datetime'
import { translate as t } from '../plugins/i18n'

// 异步任务控制台：把两个可选插件（tasks 执行器 + scheduler 触发器）的观测接口
// 拼成一张运维面板。插件未启用时接口是 404，页面按「未启用」渲染空态而非报错。

const permissionStore = usePermissionStore()
// 投递会真实触发任务执行，按 task:write 控制（路由级已挡过 task:read）
const canDispatch = computed(() => permissionStore.can('task:write'))

const loading = ref(false)
const tasksEnabled = ref(true)
const schedulerEnabled = ref(true)
const status = ref<TaskStatus | null>(null)
const scheduler = ref<SchedulerStatus | null>(null)
const lastUpdated = ref<string | null>(null)
const autoRefresh = ref(true)
const recentLimit = ref(20)

async function load() {
  loading.value = true
  try {
    // 两个插件相互独立：任一未启用/无权限都不影响另一块展示
    const [tasksRes, schedulerRes] = await Promise.allSettled([
      getTaskStatus({ limit: recentLimit.value }),
      getSchedulerJobs(),
    ])
    if (tasksRes.status === 'fulfilled') {
      status.value = tasksRes.value.data
      tasksEnabled.value = true
    } else if (tasksRes.reason instanceof PluginDisabledError) {
      status.value = null
      tasksEnabled.value = false
    }
    if (schedulerRes.status === 'fulfilled') {
      scheduler.value = schedulerRes.value.data
      schedulerEnabled.value = true
    } else if (schedulerRes.reason instanceof PluginDisabledError) {
      scheduler.value = null
      schedulerEnabled.value = false
    }
    lastUpdated.value = new Date().toISOString()
  } finally {
    loading.value = false
  }
}

// 自动刷新：默认 10s 一轮；离线或标签页失焦时暂停，避免后台空转请求
const { active } = useConnectionStatus()
const { pause, resume } = useIntervalFn(load, 10_000)
watchEffect(() => {
  if (autoRefresh.value && active.value) resume()
  else pause()
})

onMounted(load)

// ------------------------------------------------------------------ 概览卡片
// 两种后端的可观测指标不同：memory 看队列/在途 worker，redis 看 Stream 深度/待处理/消费者
const statCards = computed(() => {
  const s = status.value
  if (!s) return []
  const isRedis = s.backend === 'redis'
  return [
    {
      key: 'backend',
      label: '任务后端',
      value: isRedis ? 'Redis Streams' : '进程内队列',
      icon: 'Connection',
      color: 'var(--el-color-primary)',
    },
    {
      key: 'running',
      label: '执行中',
      value: s.running ?? 0,
      icon: 'Loading',
      color: 'var(--el-color-warning)',
    },
    {
      key: 'queue',
      label: isRedis ? '待处理' : '队列积压',
      value: isRedis ? (s.pending ?? 0) : s.queue_size,
      icon: 'Timer',
      color: 'var(--el-color-danger)',
    },
    {
      key: 'depth',
      label: isRedis ? '队列深度' : '已跟踪任务',
      value: isRedis ? (s.queue_len ?? 0) : s.tracked,
      icon: 'Coin',
      color: 'var(--el-color-success)',
    },
    {
      key: 'workers',
      label: isRedis ? '消费者' : 'Worker',
      value: isRedis ? (s.consumers ?? 0) : s.workers,
      icon: 'SetUp',
      color: 'var(--el-color-info)',
    },
  ]
})

// ------------------------------------------------------------------ 最近任务
const recentJobs = computed<TaskJob[]>(() => status.value?.recent ?? [])
const scheduledJobs = computed<ScheduledJob[]>(() => scheduler.value?.jobs ?? [])

type TagType = 'success' | 'info' | 'warning' | 'danger' | 'primary'

function stateTagType(state?: TaskState): TagType {
  if (state === 'success') return 'success'
  if (state === 'failed') return 'danger'
  if (state === 'running') return 'primary'
  if (state === 'pending' || state === 'queued') return 'warning'
  return 'info'
}

// memory 后端用 pending/running/success/failed，redis 用 queued/running/success/failed
function stateLabel(state?: TaskState): string {
  const map: Record<string, string> = {
    queued: '排队中',
    pending: '排队中',
    running: '执行中',
    success: '成功',
    failed: '失败',
    unknown: '未知',
  }
  return t(map[state ?? 'unknown'] ?? (state ?? '未知'))
}

function toDate(value?: string): number | null {
  if (!value) return null
  const normalized = /(Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`
  const ms = new Date(normalized).getTime()
  return Number.isNaN(ms) ? null : ms
}

/** 耗时：已完成的用结束时间，未完成的按当前时间算在途时长 */
function duration(row: TaskJob): string {
  const start = toDate(row.started_at)
  if (start === null) return '-'
  const end = toDate(row.finished_at) ?? Date.now()
  const ms = Math.max(0, end - start)
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`
}

function shortId(id?: string): string {
  return id ? `${id.slice(0, 8)}…` : '-'
}

// ------------------------------------------------------------------ 任务详情
const detailVisible = ref(false)
const detailRow = ref<TaskJob | null>(null)

function openDetail(row: TaskJob) {
  detailRow.value = row
  detailVisible.value = true
}

// 按 job_id 精确定位：redis 后端没有全局列表时，这是回看单条任务的入口
const queryId = ref('')
const querying = ref(false)

async function openJobById(jobId: string) {
  if (!jobId) return
  querying.value = true
  try {
    const res = await getTaskStatus({ jobId })
    detailRow.value = res.data.job ?? { job_id: jobId, state: 'unknown' }
    detailVisible.value = true
  } finally {
    querying.value = false
  }
}

function onQueryJob() {
  openJobById(queryId.value.trim())
}

// ------------------------------------------------------------------ 手动投递
const dispatchVisible = ref(false)
const dispatching = ref(false)
const dispatchForm = reactive({ name: '', argsText: '[]', kwargsText: '{}', retries: 0 })
// 后端返回的本进程已知任务名（redis 后端 Web 进程为空时可手输）
const registeredNames = computed(() => status.value?.registered ?? [])

function openDispatch() {
  dispatchForm.name = registeredNames.value[0] ?? ''
  dispatchForm.argsText = '[]'
  dispatchForm.kwargsText = '{}'
  dispatchForm.retries = 0
  dispatchVisible.value = true
}

function parseJsonArray(text: string): unknown[] | null {
  try {
    const value = JSON.parse(text || '[]')
    return Array.isArray(value) ? value : null
  } catch {
    return null
  }
}

function parseJsonObject(text: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(text || '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null
  } catch {
    return null
  }
}

async function onDispatch() {
  const name = dispatchForm.name.trim()
  if (!name) {
    ElMessage.warning(t('请填写任务名'))
    return
  }
  const args = parseJsonArray(dispatchForm.argsText)
  if (args === null) {
    ElMessage.error(t('位置参数需为 JSON 数组'))
    return
  }
  const kwargs = parseJsonObject(dispatchForm.kwargsText)
  if (kwargs === null) {
    ElMessage.error(t('关键字参数需为 JSON 对象'))
    return
  }
  dispatching.value = true
  try {
    const res = await dispatchTask({
      name,
      args,
      kwargs,
      retries: dispatchForm.retries,
    })
    ElMessage.success(t('任务已投递：{id}', { id: res.data.job_id }))
    dispatchVisible.value = false
    await load()
  } finally {
    dispatching.value = false
  }
}

function onLimitChange(size: number) {
  recentLimit.value = size
  load()
}
</script>

<template>
  <div class="admin-page">
    <!-- 插件未启用：不报错，直接给出开启方式 -->
    <el-card v-if="!tasksEnabled" class="admin-card" shadow="never">
      <el-empty :description="t('异步任务插件未启用')">
        <div class="disabled-tip">
          {{ t('在 .env 中设置 ENABLE_TASKS=true（分布式后端再设 TASK_BACKEND=redis）后重启服务即可在本页观测任务') }}
        </div>
      </el-empty>
    </el-card>

    <template v-else>
      <el-row :gutter="16">
        <el-col v-for="card in statCards" :key="card.key" :xs="12" :sm="8" :lg="4">
          <el-card class="stat-card" shadow="hover">
            <div class="stat-card__body">
              <el-icon class="stat-card__icon" :style="{ background: card.color }">
                <component :is="card.icon" />
              </el-icon>
              <div>
                <div class="stat-card__label">{{ t(card.label) }}</div>
                <div class="stat-card__value">{{ card.value }}</div>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card v-if="schedulerEnabled && scheduledJobs.length" shadow="never">
        <template #header>
          <div class="admin-toolbar">
            <span class="admin-toolbar__title">
              {{ t('定时计划') }}
              <span class="admin-muted">
                · {{ t('时区 {tz}', { tz: scheduler?.timezone ?? 'UTC' }) }}
              </span>
            </span>
          </div>
        </template>

        <el-table v-loading="loading" :data="scheduledJobs" border stripe size="small">
          <el-table-column prop="name" :label="t('任务名')" min-width="180" />
          <el-table-column prop="cron" label="Cron" width="140" />
          <el-table-column :label="t('下次触发')" width="180">
            <template #default="{ row }">{{ formatTime(row.next_run_at) }}</template>
          </el-table-column>
          <el-table-column :label="t('上次触发')" width="180">
            <template #default="{ row }">{{ formatTime(row.last_run_at) }}</template>
          </el-table-column>
          <el-table-column :label="t('最近任务')" min-width="120">
            <template #default="{ row }">
              <el-link v-if="row.last_job_id" type="primary" @click="openJobById(row.last_job_id)">
                {{ shortId(row.last_job_id) }}
              </el-link>
              <span v-else class="admin-muted">-</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card class="admin-card" shadow="never">
        <template #header>
          <div class="admin-toolbar">
            <span class="admin-toolbar__title">
              {{ t('最近任务') }}
              <span v-if="lastUpdated" class="admin-toolbar__time">
                {{ t('更新于 {time}', { time: formatTime(lastUpdated) }) }}
              </span>
            </span>
            <div class="admin-toolbar__actions">
              <el-input
                v-model="queryId"
                class="job-query"
                clearable
                :placeholder="t('按 job_id 查询')"
                @keyup.enter="onQueryJob"
              />
              <el-button :loading="querying" @click="onQueryJob">{{ t('查询') }}</el-button>
              <span class="switch-wrap">
                <span class="admin-muted">{{ t('自动刷新') }}</span>
                <el-switch v-model="autoRefresh" size="small" />
              </span>
              <el-button :loading="loading" @click="load">{{ t('刷新') }}</el-button>
              <el-button v-if="canDispatch" type="primary" plain @click="openDispatch">
                {{ t('投递任务') }}
              </el-button>
            </div>
          </div>
        </template>

        <el-table v-loading="loading" :data="recentJobs" border stripe>
          <el-table-column :label="t('任务名')" min-width="160">
            <template #default="{ row }">{{ row.name || '-' }}</template>
          </el-table-column>
          <el-table-column :label="t('状态')" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="stateTagType(row.state)" size="small">
                {{ stateLabel(row.state) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('重试次数')" width="90" align="center">
            <template #default="{ row }">{{ row.attempt ?? 0 }}</template>
          </el-table-column>
          <el-table-column :label="t('入队时间')" width="180">
            <template #default="{ row }">{{ formatTime(row.enqueued_at) }}</template>
          </el-table-column>
          <el-table-column :label="t('耗时')" width="110" align="right">
            <template #default="{ row }">{{ duration(row) }}</template>
          </el-table-column>
          <el-table-column :label="t('任务 ID')" width="120">
            <template #default="{ row }">
              <span class="admin-muted">{{ shortId(row.job_id) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('操作')" width="90" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openDetail(row)">{{ t('详情') }}</el-button>
            </template>
          </el-table-column>
          <template #empty>{{ t('暂无任务记录') }}</template>
        </el-table>

        <div class="admin-pager">
          <el-select v-model="recentLimit" class="limit-select" @change="onLimitChange">
            <el-option v-for="size in [10, 20, 50, 100]" :key="size" :label="`${size} ${t('条')}`" :value="size" />
          </el-select>
        </div>
      </el-card>
    </template>

    <!-- 任务详情 -->
    <el-drawer v-model="detailVisible" :title="t('任务详情')" size="460px">
      <el-descriptions v-if="detailRow" :column="1" border size="small">
        <el-descriptions-item :label="t('任务 ID')">
          <span class="detail-mono">{{ detailRow.job_id }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('任务名')">{{ detailRow.name || '-' }}</el-descriptions-item>
        <el-descriptions-item :label="t('状态')">
          <el-tag :type="stateTagType(detailRow.state)" size="small">
            {{ stateLabel(detailRow.state) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('重试次数')">{{ detailRow.attempt ?? 0 }}</el-descriptions-item>
        <el-descriptions-item :label="t('入队时间')">
          {{ formatTime(detailRow.enqueued_at) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('开始时间')">
          {{ formatTime(detailRow.started_at) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('结束时间')">
          {{ formatTime(detailRow.finished_at) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('耗时')">{{ duration(detailRow) }}</el-descriptions-item>
      </el-descriptions>

      <template v-if="detailRow?.error || detailRow?.last_error">
        <h4 class="detail-title">{{ t('错误信息') }}</h4>
        <pre class="detail-body">{{ detailRow.error || detailRow.last_error }}</pre>
      </template>
      <template v-if="detailRow?.result">
        <h4 class="detail-title">{{ t('返回结果') }}</h4>
        <pre class="detail-body">{{ detailRow.result }}</pre>
      </template>
    </el-drawer>

    <!-- 手动投递 -->
    <el-dialog v-model="dispatchVisible" :title="t('投递任务')" width="480px">
      <el-form label-width="110px">
        <el-form-item :label="t('任务名')">
          <el-select
            v-model="dispatchForm.name"
            filterable
            allow-create
            default-first-option
            class="form-control"
            :placeholder="t('选择已注册任务')"
          >
            <el-option v-for="name in registeredNames" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('位置参数')">
          <el-input v-model="dispatchForm.argsText" placeholder="[]" />
        </el-form-item>
        <el-form-item :label="t('关键字参数')">
          <el-input v-model="dispatchForm.kwargsText" placeholder="{}" />
        </el-form-item>
        <el-form-item :label="t('重试次数')">
          <el-input-number v-model="dispatchForm.retries" :min="0" :max="10" />
        </el-form-item>
      </el-form>
      <div class="dispatch-tip">
        {{ t('任务须已用 @task_manager.task 注册，且参数可 JSON 序列化；处理器应幂等（分布式后端为 at-least-once）') }}
      </div>
      <template #footer>
        <el-button @click="dispatchVisible = false">{{ t('取消') }}</el-button>
        <el-button type="primary" :loading="dispatching" @click="onDispatch">
          {{ t('投递') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.stat-card {
  margin-bottom: 0;
}

.stat-card__body {
  display: flex;
  align-items: center;
  gap: 12px;
}

.stat-card__icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  color: var(--el-color-white);
  font-size: 20px;
}

.stat-card__label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.stat-card__value {
  margin-top: 2px;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.2;
  word-break: break-all;
}

.admin-toolbar__time {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.job-query {
  width: 180px;
}

.switch-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.limit-select {
  width: 120px;
}

.form-control {
  width: 100%;
}

.dispatch-tip {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.disabled-tip {
  max-width: 460px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.detail-title {
  margin: 18px 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.detail-body {
  margin: 0;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.detail-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>
