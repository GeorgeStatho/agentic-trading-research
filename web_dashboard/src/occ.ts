const OCC_SYMBOL = /^([A-Z.]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "ORCL261120P00121000" -> "ORCL 20 Nov $121P". Anything else comes back unchanged. */
export function formatOccSymbol(symbol: string): string {
  const match = OCC_SYMBOL.exec(symbol);
  if (!match) {
    return symbol;
  }
  const [, root, , month, day, side, strikeThousandths] = match;
  const strike = Number(strikeThousandths) / 1000;
  return `${root} ${Number(day)} ${MONTHS[Number(month) - 1]} $${strike}${side}`;
}
