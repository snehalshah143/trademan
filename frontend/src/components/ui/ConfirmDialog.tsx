import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel?: string
  confirmVariant?: 'danger' | 'primary'
  onConfirm: () => void
  onCancel?: () => void
  children?: React.ReactNode
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  confirmVariant = 'primary',
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  const handleCancel = () => {
    onCancel?.()
    onOpenChange(false)
  }

  const handleConfirm = () => {
    onConfirm()
    onOpenChange(false)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50 animate-fade-in" />
        <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-md bg-surface-1 border border-border-default rounded-lg shadow-modal p-6 animate-fade-in">
          <div className="flex items-start justify-between mb-4">
            <Dialog.Title className="text-text-primary font-semibold text-base">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <button className="text-text-muted hover:text-text-primary ml-4">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {description && (
            <Dialog.Description className="text-text-secondary text-sm mb-4">{description}</Dialog.Description>
          )}

          {children && <div className="mb-4">{children}</div>}

          <div className="flex justify-end gap-3 mt-6">
            <button
              onClick={handleCancel}
              className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary bg-surface-3 hover:bg-surface-4 border border-border-default rounded-md transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              className={cn(
                'px-4 py-2 text-sm font-medium rounded-md transition-colors',
                confirmVariant === 'danger'
                  ? 'bg-red-600 hover:bg-red-500 text-white'
                  : 'bg-accent-blue hover:bg-blue-500 text-white'
              )}
            >
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
