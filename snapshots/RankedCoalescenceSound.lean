import Std.Tactic

namespace NewMathDiscovery.RankedCoalescence

/-- Exact finite iteration, with the zeroth iterate equal to the input. -/
def iterate {α : Type} (f : α → α) : Nat → α → α
  | 0 => fun x => x
  | k + 1 => fun x => f (iterate f k x)

@[simp]
theorem iterate_zero {α : Type} (f : α → α) (x : α) :
    iterate f 0 x = x := rfl

@[simp]
theorem iterate_succ {α : Type} (f : α → α) (k : Nat) (x : α) :
    iterate f (k + 1) x = f (iterate f k x) := rfl

/-- Iterating for `a + b` steps is iteration for `b`, then for `a`. -/
theorem iterate_add {α : Type} (f : α → α) (a b : Nat) (x : α) :
    iterate f (a + b) x = iterate f a (iterate f b x) := by
  induction a with
  | zero => simp only [Nat.zero_add, iterate_zero]
  | succ a ih =>
      simp only [Nat.succ_add, iterate_succ, ih]

/-- A fixed point remains fixed under every finite iterate. -/
theorem iterate_fixed {α : Type} (f : α → α) (one : α)
    (hfix : f one = one) (k : Nat) :
    iterate f k one = one := by
  induction k with
  | zero => rfl
  | succ k ih =>
      simp only [iterate_succ, ih, hfix]

/-- `x` reaches the distinguished terminal element under finite iteration. -/
def Reaches {α : Type} (f : α → α) (one x : α) : Prop :=
  ∃ k : Nat, iterate f k x = one

/-- Every tail of an orbit reaching a fixed terminal still reaches it. -/
theorem reaches_iterate {α : Type} (f : α → α) (one : α)
    (hfix : f one = one) {x : α} (hx : Reaches f one x) (b : Nat) :
    Reaches f one (iterate f b x) := by
  rcases hx with ⟨k, hk⟩
  refine ⟨k, ?_⟩
  rw [← iterate_add]
  rw [Nat.add_comm k b, iterate_add, hk]
  exact iterate_fixed f one hfix b

/-- Exact finite coalescence transfers convergence from target to source. -/
theorem coalescence_transfer {α : Type} (f : α → α) (one : α)
    (hfix : f one = one) {x y : α} {a b : Nat}
    (hcoalesce : iterate f a x = iterate f b y)
    (hy : Reaches f one y) :
    Reaches f one x := by
  rcases reaches_iterate f one hfix hy b with ⟨k, hk⟩
  refine ⟨k + a, ?_⟩
  calc
    iterate f (k + a) x = iterate f k (iterate f a x) :=
      iterate_add f k a x
    _ = iterate f k (iterate f b y) := by rw [hcoalesce]
    _ = one := hk

/--
Abstract soundness of a ranked coalescence system.

`edge c a b c'` means that the source and target configurations have an exact
`a`/`b`-step coalescence certificate. The well-founded relation is the pullback
of the declared rank order to configurations.
-/
theorem ranked_coalescence_sound
    {α C W : Type}
    (f : α → α)
    (one : α)
    (decode : C → α)
    (rank : C → W)
    (lt : W → W → Prop)
    (edge : C → Nat → Nat → C → Prop)
    (hfix : f one = one)
    (hwf : WellFounded (fun c' c => lt (rank c') (rank c)))
    (hprogress : ∀ c, decode c ≠ one → ∃ a b c', edge c a b c')
    (hexact : ∀ {c a b c'}, edge c a b c' →
      iterate f a (decode c) = iterate f b (decode c'))
    (hdecrease : ∀ {c a b c'}, edge c a b c' →
      lt (rank c') (rank c)) :
    ∀ c, Reaches f one (decode c) := by
  intro root
  apply hwf.induction root
  intro c ih
  by_cases hterminal : decode c = one
  · exact ⟨0, hterminal⟩
  · rcases hprogress c hterminal with ⟨a, b, c', hedge⟩
    exact coalescence_transfer f one hfix (hexact hedge)
      (ih c' (hdecrease hedge))

/-- Entry coverage lifts configuration soundness to every value in a domain. -/
theorem covered_ranked_coalescence_sound
    {α C W : Type}
    (f : α → α)
    (one : α)
    (decode : C → α)
    (rank : C → W)
    (lt : W → W → Prop)
    (edge : C → Nat → Nat → C → Prop)
    (Entry : C → Prop)
    (Domain : α → Prop)
    (hfix : f one = one)
    (hwf : WellFounded (fun c' c => lt (rank c') (rank c)))
    (hprogress : ∀ c, decode c ≠ one → ∃ a b c', edge c a b c')
    (hexact : ∀ {c a b c'}, edge c a b c' →
      iterate f a (decode c) = iterate f b (decode c'))
    (hdecrease : ∀ {c a b c'}, edge c a b c' →
      lt (rank c') (rank c))
    (hcoverage : ∀ x, Domain x → ∃ c, Entry c ∧ decode c = x) :
    ∀ x, Domain x → Reaches f one x := by
  have hall := ranked_coalescence_sound f one decode rank lt edge hfix hwf
    hprogress hexact hdecrease
  intro x hx
  rcases hcoverage x hx with ⟨c, _, hdecode⟩
  rw [← hdecode]
  exact hall c

#print axioms iterate_add
#print axioms reaches_iterate
#print axioms coalescence_transfer
#print axioms ranked_coalescence_sound
#print axioms covered_ranked_coalescence_sound

end NewMathDiscovery.RankedCoalescence
