import type { ModuleName } from '../types';

interface SidebarProps {
    currentModule: ModuleName;
    onSwitchModule: (module: ModuleName) => void;
    profilesCount: number;
    isOpen: boolean;
    onToggleSidebar: () => void;
    theme: 'dark' | 'light';
    onToggleTheme: () => void;
    isRefreshing: boolean;
    onRefresh: () => void;
}

export function Sidebar({
    currentModule,
    onSwitchModule,
    profilesCount,
    isOpen,
    onToggleSidebar,
    theme,
    onToggleTheme,
    isRefreshing,
    onRefresh,
}: SidebarProps) {
    if (!isOpen) return null;

    const inactiveClass = "w-full text-left px-3 py-2 rounded font-semibold transition-all flex items-center justify-between text-term-subtext hover:text-term-text hover:bg-term-hover border border-transparent";
    const activeClass = "w-full text-left px-3 py-2 rounded font-semibold transition-all flex items-center justify-between bg-emerald-500/10 text-emerald-500 border border-emerald-500/30";

    return (
        <aside
            id="sidebar"
            className="w-56 bg-term-panel border-r border-term-border flex flex-col shrink-0 h-full z-20 transition-all duration-300 min-w-[224px] max-w-[224px]"
        >
            {/* CABECERA DEL SIDEBAR */}
            <div className="h-11 border-b border-term-border flex items-center justify-between px-4 font-mono font-bold text-term-text tracking-wider uppercase text-[11px]">
                <div className="flex items-center gap-2">
                    <span>Analyzer</span>
                </div>
                <button
                    onClick={onToggleSidebar}
                    className="text-term-muted hover:text-term-text p-1 rounded"
                    title="Ocultar menú"
                >
                    <svg className="w-4 h-4 fill-current" viewBox="0 0 20 20">
                        <path
                            fillRule="evenodd"
                            d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z"
                            clipRule="evenodd"
                        />
                    </svg>
                </button>
            </div>

            {/* NAVEGACIÓN PRINCIPAL */}
            <nav className="p-3 space-y-1 flex-1 font-mono text-[11px]">
                <div className="text-[10px] text-term-muted uppercase font-bold tracking-wider px-2 mb-2">Menú</div>

                <button
                    onClick={() => onSwitchModule('matches')}
                    className={currentModule === 'matches' ? activeClass : inactiveClass}
                >
                    <span>Escáner</span>
                </button>

                <button
                    onClick={() => onSwitchModule('search')}
                    className={currentModule === 'search' ? activeClass : inactiveClass}
                >
                    <span>Buscar por Liga</span>
                </button>

                <button
                    onClick={() => onSwitchModule('leagues')}
                    className={currentModule === 'leagues' ? activeClass : inactiveClass}
                >
                    <span>Ligas</span>
                    {currentModule === 'leagues' && (
                        <button
                            onClick={onRefresh}
                            disabled={isRefreshing}
                            title={isRefreshing ? 'Actualizando...' : 'Actualizar ligas desde SoccerSTATS'}
                            className="ml-2 p-1 rounded hover:bg-emerald-500/10 border border-emerald-500/30 transition-colors text-emerald-500"
                        >
                            {isRefreshing ? (
                                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                                </svg>
                            ) : (
                                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                    <polyline points="23 4 23 10 17 10" />
                                    <polyline points="1 20 1 14 7 14" />
                                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                                </svg>
                            )}
                        </button>
                    )}
                </button>

                <button
                    onClick={() => onSwitchModule('profiles')}
                    className={currentModule === 'profiles' ? activeClass : inactiveClass}
                >
                    <span>Perfiles</span>
                    <span className="text-[10px] bg-term-bg border border-term-border rounded px-1.5 text-term-muted">
                        {profilesCount}
                    </span>
                </button>
            </nav>

            {/* SECCIÓN INFERIOR: CONMUTADOR DE TEMA */}
            <div className="p-3 border-t border-term-border font-mono text-[11px]">
                <button
                    onClick={onToggleTheme}
                    className="w-full flex items-center justify-between px-3 py-2 rounded bg-term-bg border border-term-border hover:bg-term-hover text-term-text transition-colors"
                >
                    <span className="font-semibold text-[10px] uppercase text-term-muted">Tema</span>
                    <span className="font-bold text-emerald-500">
                        {theme === 'dark' ? 'Oscuro' : 'Claro'}
                    </span>
                </button>
            </div>
        </aside>
    );
}