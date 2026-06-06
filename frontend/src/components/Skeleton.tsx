interface SkeletonProps {
  height?: number | string
  width?: number | string
  style?: React.CSSProperties
}

export function Skeleton({ height = 16, width = '100%', style }: SkeletonProps) {
  return <div className="skeleton" style={{ height, width, ...style }} />
}

export function CardSkeleton() {
  return (
    <div className="card">
      <Skeleton height={20} width="40%" />
      <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Skeleton height={12} />
        <Skeleton height={12} width="85%" />
        <Skeleton height={12} width="70%" />
      </div>
      <div style={{ marginTop: 24 }}>
        <Skeleton height={200} />
      </div>
    </div>
  )
}

export function StatsSkeleton() {
  return (
    <div className="grid-4">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="card">
          <Skeleton height={36} width="60%" style={{ margin: '0 auto' }} />
          <Skeleton height={12} width="80%" style={{ margin: '8px auto 0' }} />
        </div>
      ))}
    </div>
  )
}
