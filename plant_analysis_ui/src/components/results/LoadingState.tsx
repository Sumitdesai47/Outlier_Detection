export function LoadingState({ label = "Loading results…" }: { label?: string }) {
  return (
    <div className="flex min-h-[240px] items-center justify-center rounded-lg border bg-card">
      <div className="text-center">
        <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <p className="text-sm text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}
