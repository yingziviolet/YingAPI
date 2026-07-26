const THEME_KEY = 'gw_theme'

export type Theme = 'light' | 'dark'

export function currentTheme(): Theme {
  return (localStorage.getItem(THEME_KEY) as Theme) || 'light' // 默认白色打底
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export function toggleTheme() {
  const next: Theme = currentTheme() === 'light' ? 'dark' : 'light'
  localStorage.setItem(THEME_KEY, next)
  applyTheme(next)
  location.reload() // 图表颜色在渲染时取值,整页刷新最省事且绝对一致
}

export function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}
