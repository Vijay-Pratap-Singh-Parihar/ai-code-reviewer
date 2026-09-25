export default function DashboardPage() {
  return (
    <div className="flex flex-1 flex-col gap-2 p-6">
      <h1 className="text-lg font-semibold">Dashboard</h1>
      <p className="text-sm text-muted-foreground">
        Open PRs, recent runs, and token spend land here next.
      </p>
    </div>
  );
}
