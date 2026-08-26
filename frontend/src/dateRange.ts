export interface DateRange {
  startDate: string;
  endDate: string;
}

function formatLocalDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function oneCalendarMonthEarlier(value: Date): Date {
  // Clamp month-end dates (for example, March 31 -> February 28/29).
  const targetYear = value.getFullYear();
  const targetMonth = value.getMonth() - 1;
  const lastDay = new Date(targetYear, targetMonth + 1, 0).getDate();
  return new Date(targetYear, targetMonth, Math.min(value.getDate(), lastDay));
}

export function getDefaultDateRange(today = new Date()): DateRange {
  return {
    startDate: formatLocalDate(oneCalendarMonthEarlier(today)),
    endDate: formatLocalDate(today),
  };
}
