<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import {
  listRoles,
  createRole,
  updateRole,
  deleteRole,
  setRolePermissions,
  listPermissions,
} from '../api/rbac'
import type { Role, Permission } from '../api/rbac'
import { translate as t } from '../plugins/i18n'

const loading = ref(false)
const roles = ref<Role[]>([])
const permissions = ref<Permission[]>([])

// 权限点按 resource 分组，供配置权限对话框展示
const permGroups = computed<Record<string, Permission[]>>(() => {
  const groups: Record<string, Permission[]> = {}
  for (const p of permissions.value) {
    const key = p.resource || t('其他')
    ;(groups[key] ||= []).push(p)
  }
  return groups
})

async function loadRoles() {
  loading.value = true
  try {
    const res = await listRoles()
    roles.value = res.data
  } finally {
    loading.value = false
  }
}

async function loadPermissions() {
  try {
    const res = await listPermissions()
    permissions.value = res.data
  } catch {
    // 权限点读取失败不影响角色列表展示
  }
}

onMounted(() => {
  loadRoles()
  loadPermissions()
})

// 创建角色
const createVisible = ref(false)
const savingCreate = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({ name: '', code: '', description: '' })
const createRules = computed<FormRules>(() => ({
  name: [
    { required: true, message: t('请输入角色名称'), trigger: 'blur' },
    { min: 2, max: 32, message: t('长度为 2-32 位'), trigger: 'blur' },
  ],
  code: [
    { required: true, message: t('请输入角色 code'), trigger: 'blur' },
    {
      pattern: /^[a-zA-Z0-9_]+$/,
      message: t('只能包含字母、数字、下划线'),
      trigger: 'blur',
    },
  ],
}))

function openCreate() {
  createForm.name = ''
  createForm.code = ''
  createForm.description = ''
  createVisible.value = true
}

async function onCreate() {
  if (!createFormRef.value) return
  await createFormRef.value.validate()
  savingCreate.value = true
  try {
    await createRole({
      name: createForm.name.trim(),
      code: createForm.code.trim(),
      description: createForm.description.trim(),
    })
    ElMessage.success(t('创建成功'))
    createVisible.value = false
    loadRoles()
  } finally {
    savingCreate.value = false
  }
}

// 编辑角色
const editVisible = ref(false)
const savingEdit = ref(false)
const editFormRef = ref<FormInstance>()
const editingId = ref<number | null>(null)
const editForm = reactive({ name: '', description: '' })
const editRules = computed<FormRules>(() => ({
  name: [
    { required: true, message: t('请输入角色名称'), trigger: 'blur' },
    { min: 2, max: 32, message: t('长度为 2-32 位'), trigger: 'blur' },
  ],
}))

function openEdit(row: Role) {
  editingId.value = row.id
  editForm.name = row.name
  editForm.description = row.description
  editVisible.value = true
}

async function onEditSave() {
  if (!editFormRef.value || editingId.value === null) return
  await editFormRef.value.validate()
  savingEdit.value = true
  try {
    await updateRole(editingId.value, {
      name: editForm.name.trim(),
      description: editForm.description.trim(),
    })
    ElMessage.success(t('更新成功'))
    editVisible.value = false
    loadRoles()
  } finally {
    savingEdit.value = false
  }
}

// 配置角色权限
const permVisible = ref(false)
const savingPerm = ref(false)
const permRoleId = ref<number | null>(null)
const checkedPerms = ref<string[]>([])

function openPerm(row: Role) {
  permRoleId.value = row.id
  checkedPerms.value = row.permissions.map((p) => p.code)
  permVisible.value = true
}

async function onSavePerm() {
  if (permRoleId.value === null) return
  savingPerm.value = true
  try {
    await setRolePermissions(permRoleId.value, checkedPerms.value)
    ElMessage.success(t('权限已更新'))
    permVisible.value = false
    loadRoles()
  } finally {
    savingPerm.value = false
  }
}

// 删除角色（系统内置角色不可删）
async function onDelete(row: Role) {
  if (row.is_system) {
    ElMessage.warning(t('系统内置角色不可删除'))
    return
  }
  try {
    await ElMessageBox.confirm(t('确定删除角色「{name}」吗？', { name: row.name }), t('删除确认'), {
      type: 'warning',
      confirmButtonText: t('删除'),
      cancelButtonText: t('取消'),
    })
  } catch {
    return
  }
  await deleteRole(row.id)
  ElMessage.success(t('删除成功'))
  loadRoles()
}
</script>

<template>
  <div class="admin-page">
    <el-card class="admin-card" shadow="never">
      <template #header>
        <div class="admin-toolbar">
          <span class="admin-toolbar__title">{{ t('角色权限管理') }}</span>
          <div class="admin-toolbar__actions">
            <el-button type="primary" v-permission="'role:write'" @click="openCreate">
              {{ t('创建角色') }}
            </el-button>
            <el-button @click="loadRoles">{{ t('刷新') }}</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="roles" border stripe row-key="id">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" :label="t('角色名称')" min-width="120" />
        <el-table-column prop="code" :label="t('角色 code')" min-width="140" />
        <el-table-column prop="description" :label="t('描述')" min-width="160" />
        <el-table-column :label="t('系统角色')" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_system" type="warning" size="small">{{ t('内置') }}</el-tag>
            <el-tag v-else type="info" size="small">{{ t('自定义') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('权限点')" min-width="260">
          <template #default="{ row }">
            <el-tag
              v-for="p in row.permissions.slice(0, 6)"
              :key="p.id"
              size="small"
              class="perm-tag"
            >
              {{ p.name }}
            </el-tag>
            <span v-if="row.permissions.length > 6" class="perm-more">
              +{{ row.permissions.length - 6 }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="t('操作')" width="240" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" v-permission="'role:write'" @click="openPerm(row)">
              {{ t('配置权限') }}
            </el-button>
            <el-button size="small" v-permission="'role:write'" @click="openEdit(row)">
              {{ t('编辑') }}
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              v-permission="'role:write'"
              :disabled="row.is_system"
              @click="onDelete(row)"
            >
              {{ t('删除') }}
            </el-button>
          </template>
        </el-table-column>
        <template #empty>{{ t('暂无角色') }}</template>
      </el-table>
    </el-card>

    <!-- 创建角色 -->
    <el-dialog v-model="createVisible" :title="t('创建角色')" width="440px">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="80px">
        <el-form-item :label="t('角色名称')" prop="name">
          <el-input v-model="createForm.name" maxlength="32" :placeholder="t('2-32 位')" />
        </el-form-item>
        <el-form-item :label="t('角色 code')" prop="code">
          <el-input
            v-model="createForm.code"
            maxlength="32"
            :placeholder="t('程序内判定依据，如 admin')"
          />
        </el-form-item>
        <el-form-item :label="t('描述')">
          <el-input v-model="createForm.description" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">{{ t('取消') }}</el-button>
        <el-button type="primary" :loading="savingCreate" @click="onCreate">
          {{ t('确定') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑角色 -->
    <el-dialog v-model="editVisible" :title="t('编辑角色')" width="440px">
      <el-form ref="editFormRef" :model="editForm" :rules="editRules" label-width="80px">
        <el-form-item :label="t('角色名称')" prop="name">
          <el-input v-model="editForm.name" maxlength="32" :placeholder="t('2-32 位')" />
        </el-form-item>
        <el-form-item :label="t('描述')">
          <el-input v-model="editForm.description" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">{{ t('取消') }}</el-button>
        <el-button type="primary" :loading="savingEdit" @click="onEditSave">
          {{ t('保存') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 配置角色权限 -->
    <el-dialog v-model="permVisible" :title="t('配置角色权限')" width="560px">
      <el-scrollbar max-height="50vh">
        <el-checkbox-group v-model="checkedPerms">
          <div v-for="(list, resource) in permGroups" :key="resource" class="perm-group">
            <div class="perm-group__title">{{ resource || t('其他') }}</div>
            <el-checkbox
              v-for="p in list"
              :key="p.id"
              :value="p.code"
              class="perm-item"
            >
              {{ p.name }}
              <span class="perm-code">{{ p.code }}</span>
            </el-checkbox>
          </div>
        </el-checkbox-group>
      </el-scrollbar>
      <template #footer>
        <el-button @click="permVisible = false">{{ t('取消') }}</el-button>
        <el-button type="primary" :loading="savingPerm" @click="onSavePerm">
          {{ t('保存') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.perm-tag {
  margin: 2px 4px 2px 0;
}
.perm-more {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.perm-group {
  margin-bottom: 12px;
}
.perm-group__title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--el-text-color-primary);
}
.perm-item {
  margin-right: 16px;
}
.perm-code {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 4px;
}
</style>
