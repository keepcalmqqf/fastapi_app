<script setup lang="ts">
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { listPermissions, createPermission } from '../api/rbac'
import type { Permission } from '../api/rbac'
import { translate as t } from '../plugins/i18n'

const loading = ref(false)
const permissions = ref<Permission[]>([])

async function loadPermissions() {
  loading.value = true
  try {
    const res = await listPermissions()
    permissions.value = res.data
  } catch {
    // 同 UserManageView：onMounted 不接管 Promise，缺这段就是 unhandled rejection
  } finally {
    loading.value = false
  }
}

onMounted(loadPermissions)

// 创建权限点（后端创建接口守卫为 role:write）
const createVisible = ref(false)
const savingCreate = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({
  name: '',
  code: '',
  resource: '',
  action: '',
  description: '',
})

const createRules = computed<FormRules>(() => ({
  name: [
    { required: true, message: t('请输入权限点名称'), trigger: 'blur' },
    { min: 2, max: 64, message: t('长度为 2-64 位'), trigger: 'blur' },
  ],
  code: [
    { required: true, message: t('请输入权限 code'), trigger: 'blur' },
    { min: 2, max: 64, message: t('长度为 2-64 位'), trigger: 'blur' },
    {
      pattern: /^[a-zA-Z0-9_:]+$/,
      message: t('只能包含字母、数字、下划线和冒号'),
      trigger: 'blur',
    },
  ],
}))

function openCreate() {
  createForm.name = ''
  createForm.code = ''
  createForm.resource = ''
  createForm.action = ''
  createForm.description = ''
  createVisible.value = true
}

async function onCreate() {
  if (!createFormRef.value) return
  await createFormRef.value.validate()
  savingCreate.value = true
  try {
    await createPermission({
      name: createForm.name.trim(),
      code: createForm.code.trim(),
      resource: createForm.resource.trim(),
      action: createForm.action.trim(),
      description: createForm.description.trim(),
    })
    ElMessage.success(t('创建成功'))
    createVisible.value = false
    loadPermissions()
  } finally {
    savingCreate.value = false
  }
}
</script>

<template>
  <div class="admin-page">
    <el-card class="admin-card" shadow="never">
      <template #header>
        <div class="admin-toolbar">
          <span class="admin-toolbar__title">{{ t('权限点管理') }}</span>
          <div class="admin-toolbar__actions">
            <el-button type="primary" v-permission="'role:write'" @click="openCreate">
              {{ t('创建权限点') }}
            </el-button>
            <el-button @click="loadPermissions">{{ t('刷新') }}</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="permissions" border stripe row-key="id">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" :label="t('权限名称')" min-width="140" />
        <el-table-column prop="code" :label="t('权限 code')" min-width="160" />
        <el-table-column prop="resource" :label="t('资源')" min-width="120" />
        <el-table-column prop="action" :label="t('操作')" min-width="100" />
        <el-table-column prop="description" :label="t('描述')" min-width="200" />
        <template #empty>{{ t('暂无权限点') }}</template>
      </el-table>
    </el-card>

    <!-- 创建权限点 -->
    <el-dialog v-model="createVisible" :title="t('创建权限点')" width="460px">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="84px">
        <el-form-item :label="t('权限名称')" prop="name">
          <el-input v-model="createForm.name" maxlength="64" :placeholder="t('如 用户查看')" />
        </el-form-item>
        <el-form-item :label="t('权限 code')" prop="code">
          <el-input
            v-model="createForm.code"
            maxlength="64"
            :placeholder="t('程序内判定依据，如 user:read')"
          />
        </el-form-item>
        <el-form-item :label="t('资源')">
          <el-input
            v-model="createForm.resource"
            maxlength="64"
            :placeholder="t('业务模块，如 user / member')"
          />
        </el-form-item>
        <el-form-item :label="t('操作')">
          <el-input
            v-model="createForm.action"
            maxlength="64"
            :placeholder="t('行为动词，如 read / write')"
          />
        </el-form-item>
        <el-form-item :label="t('描述')">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="2"
            maxlength="255"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">{{ t('取消') }}</el-button>
        <el-button type="primary" :loading="savingCreate" @click="onCreate">
          {{ t('确定') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 页面容器、工具栏样式统一走 src/styles/admin.css */
</style>
