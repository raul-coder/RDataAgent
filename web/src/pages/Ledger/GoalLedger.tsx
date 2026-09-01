import LedgerTable from './LedgerTable';

/** 整体目标台账（FR-D1）：单元 × 年度 × 月度的商业/商解目标 */
export default function GoalLedger() {
  return <LedgerTable ledgerKey="goal" title="整体目标台账" />;
}
