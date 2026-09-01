import LedgerTable from './LedgerTable';

/** PPL 明细台账（FR-D1）：销售机会点管线，10,000 行 */
export default function PplLedger() {
  return <LedgerTable ledgerKey="ppl" title="PPL 明细台账" />;
}
