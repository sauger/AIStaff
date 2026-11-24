import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';

export type EmployeeAppearance = 'original' | 'ink';

export const APPEARANCE_STORAGE_KEY = 'staffdeck_employee_appearance';

type AppearanceContextValue = {
  appearance: EmployeeAppearance;
  setAppearance: (next: EmployeeAppearance) => void;
  toggle: () => void;
};

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

function readInitialAppearance(): EmployeeAppearance {
  if (typeof window === 'undefined') return 'ink';
  return window.localStorage.getItem(APPEARANCE_STORAGE_KEY) === 'original' ? 'original' : 'ink';
}

export function EmployeeAppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearanceState] = useState<EmployeeAppearance>(readInitialAppearance);

  useEffect(() => {
    window.localStorage.setItem(APPEARANCE_STORAGE_KEY, appearance);
  }, [appearance]);

  const setAppearance = useCallback((next: EmployeeAppearance) => {
    setAppearanceState(next);
  }, []);

  const toggle = useCallback(() => {
    setAppearanceState((current) => (current === 'original' ? 'ink' : 'original'));
  }, []);

  return (
    <AppearanceContext.Provider value={{ appearance, setAppearance, toggle }}>
      {children}
    </AppearanceContext.Provider>
  );
}

export function useEmployeeAppearance(): AppearanceContextValue {
  return useContext(AppearanceContext) || { appearance: 'ink', setAppearance: () => {}, toggle: () => {} };
}
