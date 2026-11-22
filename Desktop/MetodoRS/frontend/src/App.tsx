import { useState, useEffect } from 'react';
import type { ModuleName, Match } from './types';
import { matchesDataset } from './data/raw_data';
import { useProfiles } from './hooks/useProfiles';
import { useLeagues } from './hooks/useLeagues';
import { useToast, Toast } from './components/Toast';

import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';

import { ScannerPage } from './pages/ScannerPage';
import { SearchLeaguePage } from './pages/SearchLeaguePage';
import { LeaguesPage } from './pages/LeaguesPage';
import { ProfilesPage } from './pages/ProfilesPage';

import { ModalAnalysis } from './components/ModalAnalysis';
import { ModalProfile } from './components/ModalProfile';

export function App() {
    const [currentModule, setCurrentModule] = useState<ModuleName>('matches');
    const [sidebarOpen, setSidebarOpen] = useState<boolean>(true);
    const [theme, setTheme] = useState<'dark' | 'light'>('dark');

    const [selectedScannerProfileId, setSelectedScannerProfileId] = useState<string>('');
    const [analysisMatch, setAnalysisMatch] = useState<Match | null>(null);

    const [profileModalOpen, setProfileModalOpen] = useState<boolean>(false);
    const [pendingLeagueNames, setPendingLeagueNames] = useState<string[]>([]);

    const {
        profiles,
        createEmptyProfile,
        renameProfile,
        deleteProfile,
        removeLeagueFromProfile,
        addLeaguesToProfile,
        createProfileAndAddLeagues,
    } = useProfiles();

    const { toast, showToast, closeToast } = useToast();

    const {
        leagues,
        lastUpdated,
        isRefreshing,
        refreshError,
        refreshSuccess,
        backendAvailable,
        startRefresh,
        clearRefreshMessages,
    } = useLeagues();

    // Sincronizar clase html para light-mode
    useEffect(() => {
        const html = document.documentElement;
        if (theme === 'light') {
            html.classList.add('light-mode');
        } else {
            html.classList.remove('light-mode');
        }
    }, [theme]);

    function toggleTheme() {
        setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));
    }

    function handleAnalyze(id: number) {
        const found = matchesDataset.find(m => m.id === id);
        if (found) setAnalysisMatch(found);
    }

    function handleOpenProfileModal(leagueNames: string[]) {
        if (!leagueNames.length) {
            showToast('No hay ligas para agregar.');
            return;
        }
        setPendingLeagueNames(leagueNames);
        setProfileModalOpen(true);
    }

    function handleConfirmAddToProfile(targetProfileId: string, isNew: boolean, newName: string) {
        if (isNew) {
            const ok = createProfileAndAddLeagues(newName, pendingLeagueNames);
            if (!ok) {
                showToast('No se pudo crear el perfil.');
                return;
            }
            showToast(`${pendingLeagueNames.length} ligas agregadas a "${newName}".`);
        } else {
            const prof = profiles.find(p => p.id === targetProfileId);
            if (!prof) return;
            const prevCount = prof.leagues.length;
            addLeaguesToProfile(targetProfileId, pendingLeagueNames);
            const added = pendingLeagueNames.filter(n => !prof.leagues.includes(n)).length;
            const skipped = pendingLeagueNames.length - added;
            showToast(
                `${added} ${added === 1 ? 'liga agregada' : 'ligas agregadas'} a "${prof.name}"` +
                (skipped ? ` (${skipped} ya estaban)` : '') + '.'
            );
        }
        setProfileModalOpen(false);
    }

    function handleScanProfile(id: string) {
        setSelectedScannerProfileId(id);
        setCurrentModule('matches');
    }

    return (
        <div className="h-full w-full flex bg-term-bg text-term-text font-sans text-xs antialiased selection:bg-emerald-500/30 selection:text-emerald-300">
            {/* SIDEBAR */}
            <Sidebar
                currentModule={currentModule}
                onSwitchModule={setCurrentModule}
                profilesCount={profiles.length}
                isOpen={sidebarOpen}
                onToggleSidebar={() => setSidebarOpen(prev => !prev)}
                theme={theme}
                onToggleTheme={toggleTheme}
                isRefreshing={isRefreshing}
                onRefresh={startRefresh}
            />

            {/* CONTENEDOR PRINCIPAL DERECHO */}
            <div className="flex-1 flex flex-col overflow-hidden min-w-0 w-0">
                <Header
                    sidebarOpen={sidebarOpen}
                    onToggleSidebar={() => setSidebarOpen(true)}
                />

                {/* VISTAS MÓDULO */}
                {currentModule === 'matches' && (
                    <ScannerPage
                        profiles={profiles}
                        selectedProfileId={selectedScannerProfileId}
                        onSelectProfile={setSelectedScannerProfileId}
                        onAnalyze={handleAnalyze}
                    />
                )}

                {currentModule === 'search' && (
                    <SearchLeaguePage
                        onAnalyze={handleAnalyze}
                    />
                )}

                {currentModule === 'leagues' && (
                    <LeaguesPage
                        onOpenProfileModal={handleOpenProfileModal}
                        leagues={leagues}
                        lastUpdated={lastUpdated}
                        isRefreshing={isRefreshing}
                        refreshError={refreshError}
                        refreshSuccess={refreshSuccess}
                        backendAvailable={backendAvailable}
                        onRefresh={startRefresh}
                        onClearMessages={clearRefreshMessages}
                    />
                )}

                {currentModule === 'profiles' && (
                    <ProfilesPage
                        profiles={profiles}
                        onCreateProfile={(name) => {
                            const ok = createEmptyProfile(name);
                            if (ok) showToast(`Perfil "${name}" creado.`);
                            else showToast('No se pudo crear el perfil (nombre inválido o duplicado).');
                        }}
                        onRenameProfile={renameProfile}
                        onDeleteProfile={deleteProfile}
                        onRemoveLeague={removeLeagueFromProfile}
                        onScanProfile={handleScanProfile}
                    />
                )}
            </div>

            {/* MODALES */}
            <ModalAnalysis
                match={analysisMatch}
                onClose={() => setAnalysisMatch(null)}
            />

            <ModalProfile
                isOpen={profileModalOpen}
                leagueNames={pendingLeagueNames}
                profiles={profiles}
                onClose={() => setProfileModalOpen(false)}
                onConfirmAdd={handleConfirmAddToProfile}
            />

            {/* TOAST */}
            <Toast message={toast} onClose={closeToast} />
        </div>
    );
}