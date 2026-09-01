import LedgerTable from './LedgerTable';

/** 商业市场台账（FR-D1）：合同级明细，15,000 行 */
export default function CommercialLedger() {
  return <LedgerTable ledgerKey="contract" title="商业市场台账" />;
}
