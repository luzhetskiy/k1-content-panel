/**
 * Подписи статусов партии — общие для списка партий и страницы одной партии.
 *
 * Вынесено из ArticlesPage.tsx, когда статус понадобился и на BatchPage:
 * две копии одной карты разошлись бы при первой же правке формулировки, а
 * менеджер увидел бы у одной и той же партии разные подписи на двух экранах.
 */

export const BATCH_STATUS: Record<string, { color: string; label: string }> = {
  topics_pending: { color: 'processing', label: 'Подбираются темы' },
  topics_review: { color: 'warning', label: 'Темы на согласовании' },
  running: { color: 'processing', label: 'Генерируется' },
  done: { color: 'success', label: 'Готово' },
  failed: { color: 'error', label: 'Ошибка' },
  paused: { color: 'warning', label: 'Приостановлена' },
}

/**
 * Как показывать партию в running: сама по себе «Генерируется» означает лишь
 * «кто-то нажал кнопку» — статус выставляет эндпоинт до постановки в очередь.
 * Реальное положение дел приходит в batch.runtime_state.
 */
export const RUNTIME_STATE: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: 'Ждёт очереди' },
  working: { color: 'processing', label: 'Генерируется' },
  pausing: { color: 'warning', label: 'Останавливается' },
  stuck: { color: 'error', label: 'Похоже, зависла' },
}

/** Статусы категории в разделе «Метатеги категорий». */
export const CATEGORY_META_STATUS: Record<string, { color: string; label: string }> = {
  new: { color: 'default', label: 'Ещё не обрабатывалась' },
  queued: { color: 'default', label: 'В очереди' },
  in_work: { color: 'processing', label: 'В работе' },
  done: { color: 'success', label: 'Готово' },
  failed: { color: 'error', label: 'Ошибка' },
  skipped: { color: 'default', label: 'Пропущена' },
}
