const THEME_KEY = 'gw_theme'

export type Theme = 'light' | 'dark'

export function currentTheme(): Theme {
  return (localStorage.getItem(THEME_KEY) as Theme) || 'light' // 默认浅色
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('light', theme === 'light')
}

export function toggleTheme() {
  const next: Theme = currentTheme() === 'light' ? 'dark' : 'light'
  localStorage.setItem(THEME_KEY, next)
  applyTheme(next)
  // 图表颜色在渲染时取值,整页刷新最省事且绝对一致
  location.reload()
}

export function isLight(): boolean {
  return document.documentElement.classList.contains('light')
}
