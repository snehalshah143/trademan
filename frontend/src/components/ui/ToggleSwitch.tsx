import * as Switch from '@radix-ui/react-switch'

interface ToggleSwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  size?: 'sm' | 'md'
  disabled?: boolean
}

export function ToggleSwitch({ checked, onCheckedChange, size = 'md', disabled }: ToggleSwitchProps) {
  const trackClass = size === 'sm'
    ? 'w-7 h-3.5'
    : 'w-9 h-5'
  const thumbClass = size === 'sm'
    ? 'w-2.5 h-2.5 translate-x-0.5 data-[state=checked]:translate-x-4'
    : 'w-3.5 h-3.5 translate-x-0.5 data-[state=checked]:translate-x-[18px]'

  return (
    <Switch.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      className={`${trackClass} rounded-full border border-border-default bg-surface-3 data-[state=checked]:bg-accent-blue transition-colors disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      <Switch.Thumb
        className={`block ${thumbClass} rounded-full bg-text-muted data-[state=checked]:bg-white transition-transform`}
      />
    </Switch.Root>
  )
}
