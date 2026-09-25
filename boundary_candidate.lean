import Std.Tactic

namespace JevBoundary

def LostParent {α : Type} (removed : List α) (edge : α → α → Prop) (v : α) : Prop :=
  ∃ u, u ∈ removed ∧ edge u v

def Repaired {α : Type} (removed : List α) (edge : α → α → Prop) (u v : α) : Prop :=
  edge u v ∧ u ∉ removed ∧ v ∉ removed ∧ ¬ LostParent removed edge v

theorem lost_parent_mem_cover {α : Type} (removed : List α) (edge : α → α → Prop)
    (children : α → List α) (hchildren : ∀ u v, edge u v → v ∈ children u)
    (v : α) (h : LostParent removed edge v) : v ∈ removed.flatMap children := by
  rcases h with ⟨u, hu, huv⟩
  exact List.mem_flatMap.mpr ⟨u, hu, hchildren u v huv⟩

theorem repaired_parent_coverage {α β : Type} (removed : List α) (edge : α → α → Prop)
    (label : α → β)
    (hcoverage : ∀ v, (∃ u, edge u v) → ∀ c, ∃ u, edge u v ∧ label u = c)
    (v : α) (h : ∃ u, Repaired removed edge u v) :
    ∀ c, ∃ u, Repaired removed edge u v ∧ label u = c := by
  rcases h with ⟨u, hu⟩
  rcases hu with ⟨huv, hu_keep, hv_keep, hnot_lost⟩
  intro c
  obtain ⟨w, hwv, hwlabel⟩ := hcoverage v ⟨u, huv⟩ c
  have hw_keep : w ∉ removed := by
    intro hw_removed
    exact hnot_lost ⟨w, hw_removed, hwv⟩
  exact ⟨w, ⟨hwv, hw_keep, hv_keep, hnot_lost⟩, hwlabel⟩

theorem repaired_roots_mem_cover {α : Type} (removed roots : List α) (edge : α → α → Prop)
    (children : α → List α) (hchildren : ∀ u v, edge u v → v ∈ children u)
    (hroots : ∀ v, (¬ ∃ u, edge u v) → v ∈ roots)
    (v : α) (hkeep : v ∉ removed) (hnewroot : ¬ ∃ u, Repaired removed edge u v) :
    v ∈ roots ++ removed.flatMap children := by
  by_cases hlost : LostParent removed edge v
  · have hcover := lost_parent_mem_cover removed edge children hchildren v hlost
    exact List.mem_append.mpr (Or.inr hcover)
  · have hno_parent : ¬ ∃ u, edge u v := by
      rintro ⟨u, huv⟩
      have hu_keep : u ∉ removed := by
        intro hu_removed
        exact hlost ⟨u, hu_removed, huv⟩
      exact hnewroot ⟨u, ⟨huv, hu_keep, hkeep, hlost⟩⟩
    exact List.mem_append.mpr (Or.inl (hroots v hno_parent))

theorem repaired_path_is_original {α : Type} (removed : List α) (edge : α → α → Prop)
    (path : Nat → α)
    (h : ∀ n, Repaired removed edge (path n) (path (n+1))) :
    ∀ n, edge (path n) (path (n+1)) := by
  intro n
  exact (h n).1

#print axioms lost_parent_mem_cover
#print axioms repaired_parent_coverage
#print axioms repaired_roots_mem_cover
#print axioms repaired_path_is_original

end JevBoundary
