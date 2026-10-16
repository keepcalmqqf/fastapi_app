<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { listUsers, createUser, updateUser, deleteUser } from '../api/user'
import type { AdminUser } from '../api/user'
import { listRoles, getUserRoles, setUserRoles } from '../api/rbac'
import type { Role } from '../api/rbac'
import { useAuthStore } from '../stores/auth'
import { usePermissionStore } from '../stores/permission'
import { formatTime } from '../utils/datetime'
import { translate as t } from '../plugins/i18n'

const authStore = useAuthStore()
// 当前登录用户是否为超级管理员：后端规定仅超级管理员可分配 super_admin 角色，
// 前端据此放开/禁用该高权选项的勾选（其余角色普通管理员即可分配）。
const permissionStore = usePermissionStore()
const isSuperAdmin = computed(() => permissionStore.isSuperAdmin)

// 角色清单（分配角色对话框用）
const allRoles = ref<Role[]>([])
async function loadRoles() {
  try {
    const res = await listRoles()
    allRoles.value = res.data
  } catch {
    // 角色清单读取失败不影响用户列表
  }
}

const loading = ref(false)
const users = ref<AdminUser[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

async function loadUsers() {
  loading.value = true
  try {
    const res = await listUsers(page.value, pageSize.value)
    users.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadUsers()
}

function onSizeChange(size: number) {
  pageSize.value = size
  page.value = 1
  loadUsers()
}

// 编辑对话框
const editVisible = ref(false)
const saving = ref(false)
const editFormRef = ref<FormInstance>()
const editingId = ref<number | null>(null)
const editForm = reactive({ name: '', is_active: true })

const editRules = computed<FormRules>(() => ({
  name: [
    { required: true, message: t('请输入昵称'), trigger: 'blur' },
    { min: 2, max: 32, message: t('昵称长度为 2-32 位'), trigger: 'blur' },
  ],
}))

// 添加用户对话框
const createVisible = ref(false)
const savingCreate = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({ name: '', email: '', password: '', is_active: true })

// 校验规则与后端 CreateUser 对齐：昵称 2-32，邮箱格式，密码 8-64 且必须含字母和数字
const createRules = computed<FormRules>(() => ({
  name: [
    { required: true, message: t('请输入昵称'), trigger: 'blur' },
    { min: 2, max: 32, message: t('昵称长度为 2-32 位'), trigger: 'blur' },
  ],
  email: [
    { required: true, message: t('请输入邮箱'), trigger: 'blur' },
    { type: 'email', message: t('邮箱格式不正确'), trigger: 'blur' },
  ],
  password: [
    { required: true, message: t('请输入密码'), trigger: 'blur' },
    { min: 8, max: 64, message: t('密码长度为 8-64 位'), trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        if (value && !(/\p{L}/u.test(value) && /\d/.test(value))) {
          callback(new Error(t('密码必须同时包含字母和数字')))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}))

function openCreate() {
  createForm.name = ''
  createForm.email = ''
  createForm.password = ''
  createForm.is_active = true
  createVisible.value = true
}

async function onCreate() {
  if (!createFormRef.value) return
  await createFormRef.value.validate()
  savingCreate.value = true
  try {
    await createUser({
      name: createForm.name.trim(),
      email: createForm.email.trim(),
      password: createForm.password,
      is_active: createForm.is_active,
    })
    ElMessage.success(t('添加成功'))
    createVisible.value = false
    // 列表按 id 升序，新用户在末页，跳到最后一页以便看到刚添加的用户
    const lastPage = Math.max(1, Math.ceil((total.value + 1) / pageSize.value))
    if (lastPage !== page.value) page.value = lastPage
    loadUsers()
  } finally {
    savingCreate.value = false
  }
}

function openEdit(row: AdminUser) {
  editingId.value = row.id
  editForm.name = row.name ?? ''
  editForm.is_active = row.is_active
  editVisible.value = true
}

async function onSave() {
  if (!editFormRef.value || editingId.value === null) return
  await editFormRef.value.validate()
  saving.value = true
  try {
    await updateUser(editingId.value, {
      name: editForm.name.trim(),
      is_active: editForm.is_active,
    })
    ElMessage.success(t('更新成功'))
    editVisible.value = false
    loadUsers()
  } finally {
    saving.value = false
  }
}

async function onDelete(row: AdminUser) {
  try {
    await ElMessageBox.confirm(
      t('确定删除用户「{name}」吗？该操作为软删除。', { name: row.name ?? row.email }),
      t('删除确认'),
      { type: 'warning', confirmButtonText: t('删除'), cancelButtonText: t('取消') },
    )
  } catch {
    return
  }
  await deleteUser(row.id)
  ElMessage.success(t('删除成功'))
  // 删除当前页最后一条时回退一页，避免停在空页
  if (users.value.length === 1 && page.value > 1) {
    page.value -= 1
  }
  loadUsers()
}

// 分配角色
const roleVisible = ref(false)
const savingRole = ref(false)
const roleUserId = ref<number | null>(null)
const roleUserName = ref('')
const checkedRoles = ref<string[]>([])

async function openAssignRole(row: AdminUser) {
  roleUserId.value = row.id
  roleUserName.value = row.name ?? row.email
  checkedRoles.value = []
  roleVisible.value = true
  try {
    const res = await getUserRoles(row.id)
    checkedRoles.value = res.data.map((r) => r.code)
  } catch {
    // 读取失败保留空选项，用户仍可保存（整组替换）
  }
}

async function onSaveRole() {
  if (roleUserId.value === null) return
  savingRole.value = true
  try {
    // 非超级管理员不可分配 super_admin（后端同样拦截），提交前先过滤掉避免 403
    const payload = checkedRoles.value.filter(
      (c) => !(c === 'super_admin' && !isSuperAdmin.value),
    )
    await setUserRoles(roleUserId.value, payload)
    ElMessage.success(t('角色已更新'))
    roleVisible.value = false
    loadUsers()
  } catch {
    // 请求失败已由响应拦截器统一提示，这里仅确保 loading 状态重置
  } finally {
    savingRole.value = false
  }
}

onMounted(() => {
  loadUsers()
  loadRoles()
})
</script>

<template>
  <div class="admin-page">
    <el-card class="admin-card" shadow="never">
      <template #header>
        <div class="admin-toolbar">
          <span class="admin-toolbar__title">{{ t('用户管理') }}</span>
          <div class="admin-toolbar__actions">
            <el-button type="primary" @click="openCreate">{{ t('添加用户') }}</el-button>
            <el-button @click="loadUsers">{{ t('刷新') }}</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="users" border stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" :label="t('昵称')" min-width="120" />
        <el-table-column prop="email" :label="t('邮箱')" min-width="200" />
        <el-table-column :label="t('状态')" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? t('启用') : t('禁用') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('创建时间')" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="t('操作')" width="260" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" v-permission="'user:write'" @click="openAssignRole(row)">
              {{ t('分配角色') }}
            </el-button>
            <el-button size="small" @click="openEdit(row)">{{ t('编辑') }}</el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="row.id === authStore.user?.id"
              @click="onDelete(row)"
            >
              {{ t('删除') }}
            </el-button>
          </template>
        </el-table-column>
        <template #empty>{{ t('暂无用户') }}</template>
      </el-table>

      <div class="admin-pager">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>

    <el-dialog v-model="editVisible" :title="t('编辑用户')" width="420px">
      <el-form ref="editFormRef" :model="editForm" :rules="editRules" label-width="70px">
        <el-form-item :label="t('昵称')" prop="name">
          <el-input v-model="editForm.name" maxlength="32" :placeholder="t('2-32 位昵称')" />
        </el-form-item>
        <el-form-item :label="t('状态')">
          <el-switch
            v-model="editForm.is_active"
            :active-text="t('启用')"
            :inactive-text="t('禁用')"
            inline-prompt
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">{{ t('取消') }}</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">{{ t('保存') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createVisible" :title="t('添加用户')" width="420px">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="70px">
        <el-form-item :label="t('昵称')" prop="name">
          <el-input v-model="createForm.name" maxlength="32" :placeholder="t('2-32 位昵称')" />
        </el-form-item>
        <el-form-item :label="t('邮箱')" prop="email">
          <el-input v-model="createForm.email" :placeholder="t('登录邮箱')" />
        </el-form-item>
        <el-form-item :label="t('密码')" prop="password">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            maxlength="64"
            :placeholder="t('8-64 位，需包含字母和数字')"
          />
        </el-form-item>
        <el-form-item :label="t('状态')">
          <el-switch
            v-model="createForm.is_active"
            :active-text="t('启用')"
            :inactive-text="t('禁用')"
            inline-prompt
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

    <el-dialog v-model="roleVisible" :title="t('分配角色')" width="460px">
      <div class="assign-role__title">
        {{ t('为用户「{name}」分配角色（整组替换）', { name: roleUserName }) }}
      </div>
      <el-checkbox-group v-model="checkedRoles">
        <el-checkbox
          v-for="r in allRoles"
          :key="r.id"
          :value="r.code"
          class="assign-role__item"
          :disabled="r.code === 'super_admin' && !isSuperAdmin"
        >
          {{ r.name }}
          <span class="assign-role__code">{{ r.code }}</span>
        </el-checkbox>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="roleVisible = false">{{ t('取消') }}</el-button>
        <el-button type="primary" :loading="savingRole" @click="onSaveRole">
          {{ t('保存') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 页面容器、工具栏与分页样式统一走 src/styles/admin.css */
.assign-role__title {
  margin-bottom: 12px;
  color: var(--el-text-color-regular);
}
.assign-role__item {
  margin-right: 16px;
  margin-bottom: 8px;
}
.assign-role__code {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 4px;
}
</style>
