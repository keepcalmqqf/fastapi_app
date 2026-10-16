import type { App, Directive, DirectiveBinding } from 'vue'
import { usePermissionStore } from '../stores/permission'

// 鉴权判断：super_admin 放行一切；v-role 按角色判定，其余按权限 code 判定。
// 权限支持传入单个 code 或数组（数组表示「拥有任一即通过」）。
function authorized(binding: DirectiveBinding): boolean {
  const perm = usePermissionStore()
  if (perm.isSuperAdmin) return true
  const value = binding.value
  const codes = Array.isArray(value) ? value : [value]
  if (binding.arg === 'role') {
    return codes.some((c) => perm.hasRole(c))
  }
  return perm.hasAny(codes)
}

function apply(el: HTMLElement, binding: DirectiveBinding) {
  const ok = authorized(binding)
  if (ok) {
    el.style.display = ''
    el.removeAttribute('disabled')
    el.style.pointerEvents = ''
    el.classList.remove('is-disabled')
    return
  }
  // .disable 修饰符：禁用而非隐藏（适用于按钮等仍需占位可见的场景）
  if (binding.modifiers.disable) {
    if ('disabled' in el) {
      ;(el as unknown as { disabled: boolean }).disabled = true
    } else {
      el.setAttribute('disabled', 'disabled')
    }
    el.style.pointerEvents = 'none'
    el.classList.add('is-disabled')
  } else {
    el.style.display = 'none'
  }
}

const permissionDirective: Directive<HTMLElement, string | string[]> = {
  mounted(el, binding) {
    apply(el, binding)
  },
  updated(el, binding) {
    apply(el, binding)
  },
}

// v-role：按角色 code 鉴权，语义与 v-permission 对称
const roleDirective: Directive<HTMLElement, string | string[]> = {
  mounted(el, binding) {
    binding.arg = 'role'
    apply(el, binding)
  },
  updated(el, binding) {
    binding.arg = 'role'
    apply(el, binding)
  },
}

export function registerPermissionDirectives(app: App) {
  app.directive('permission', permissionDirective)
  app.directive('role', roleDirective)
}
