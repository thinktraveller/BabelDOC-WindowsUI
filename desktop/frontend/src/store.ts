import { create } from "zustand";

import type { Preset } from "./types";

export interface WorkbenchState {
  selectedFileIds: string[];
  params: Record<string, unknown>;
  profileId: number | null;
  presetName: string;
  setSelectedFileIds: (ids: string[]) => void;
  toggleFile: (id: string) => void;
  setParams: (params: Record<string, unknown>) => void;
  patchParams: (patch: Record<string, unknown>) => void;
  setProfileId: (id: number | null) => void;
  applyPreset: (preset: Preset) => void;
  setPresetName: (name: string) => void;
}

export const useWorkbench = create<WorkbenchState>((set) => ({
  selectedFileIds: [],
  params: {},
  profileId: null,
  presetName: "",
  setSelectedFileIds: (ids) => set({ selectedFileIds: ids }),
  toggleFile: (id) =>
    set((state) => ({
      selectedFileIds: state.selectedFileIds.includes(id)
        ? state.selectedFileIds.filter((item) => item !== id)
        : [...state.selectedFileIds, id],
    })),
  setParams: (params) => set({ params }),
  patchParams: (patch) => set((state) => ({ params: { ...state.params, ...patch } })),
  setProfileId: (id) => set({ profileId: id }),
  applyPreset: (preset) =>
    set({ params: { ...preset.params }, presetName: preset.name }),
  setPresetName: (name) => set({ presetName: name }),
}));
