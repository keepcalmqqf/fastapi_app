<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useAuthStore } from '../stores/auth'
// i18n 插件：中文原文即键，切语种后模板内的文案与校验提示一起刷新
import { translate as t } from '../plugins/i18n'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref<FormInstance>()
const loading = ref(false)

const form = reactive({
  email: '',
  password: '',
})

// 初始化入口只在「库里还没有管理员」时出现；已初始化后不再暴露该入口
const canBootstrap = ref(false)
onMounted(() => {
  // onMounted 不接管 Promise，这里必须自己 catch，否则探测失败就是 unhandled
  // rejection；探测不到就按「不展示入口」处理，属可接受的降级
  const probe = async () => {
    if (authStore.needsBootstrap === null) await authStore.checkBootstrap()
    canBootstrap.value = authStore.needsBootstrap === true
  }
  probe().catch(() => {})
})

// 校验提示也走词条：用 computed 而非常量，切换语种后重新校验即显示新语言
const rules = computed<FormRules>(() => ({
  email: [
    { required: true, message: t('请输入邮箱'), trigger: 'blur' },
    { type: 'email', message: t('邮箱格式不正确'), trigger: 'blur' },
  ],
  password: [
    { required: true, message: t('请输入密码'), trigger: 'blur' },
    { min: 8, max: 64, message: t('长度需为 8-64 位'), trigger: 'blur' },
  ],
}))

async function onSubmit() {
  if (!formRef.value) return
  // 必须用 Promise 形态而非回调形态：Element Plus 不会 await 传给 validate 的回调，
  // 于是外层 await 在「校验结束」时就 resolve——登录请求可能还没发出去，而回调内部
  // 抛出的异常也无处可去，直接变成 unhandled rejection。
  try {
    await formRef.value.validate()
  } catch {
    // 校验未通过，错误已由表单内联展示
    return
  }
  loading.value = true
  try {
    await authStore.login(form.email, form.password)
    ElMessage.success(t('登录成功'))
    // 支持 401 跳转时携带的 redirect 回跳参数
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.push(redirect)
  } catch (err) {
    // 登录失败（如邮箱或密码错误）：响应拦截器已将后端 message 带出，
    // 这里捕获并展示，避免 401 被 finally 静默吞掉
    const msg = err instanceof Error ? err.message : t('登录失败，请重试')
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card">
      <h2 class="title">FastAPI 全栈脚手架</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item :label="t('邮箱')" prop="email">
          <el-input v-model="form.email" :placeholder="t('请输入邮箱')" clearable />
        </el-form-item>
        <el-form-item :label="t('密码')" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            :placeholder="t('请输入密码')"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button class="submit-btn" type="primary" :loading="loading" @click="onSubmit">
          {{ t('登录') }}
        </el-button>
      </el-form>
      <div v-if="canBootstrap" class="footer-link">
        {{ t('尚未初始化？') }}
        <router-link to="/register">{{ t('创建首个管理员') }}</router-link>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--el-bg-color-page);
}

.login-card {
  width: 400px;
}

.title {
  margin: 0 0 24px;
  text-align: center;
  font-size: 20px;
}

.submit-btn {
  width: 100%;
}

.footer-link {
  margin-top: 16px;
  text-align: center;
  font-size: 14px;
  color: #606266;
}
</style>
