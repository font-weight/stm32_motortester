"""Finite, declared counter semantics. No producer discovery or authentication.

Ordinary admission remains SYNTHETIC-only. A separate exact image declaration
can be rehearsed with explicit fake transport; it never admits physical use.
Existing generic/synthetic identity strings keep their previous meaning.
"""
from dataclasses import dataclass, fields


DMA_DOMAIN = "completed-dma-v1"
DMA_REVISION = "0.2-draft-dma-sequence-20261019"


class ProvenanceError(ValueError):
    """An explicit producer declaration cannot be admitted or interpreted."""


@dataclass(frozen=True, slots=True)
class ProducerProfile:
    profile_id: str
    accounting_domain: str
    provenance_revision: str
    source_commit: str
    artifact_sha256: str
    expected_build_id: int
    origin: str
    config_id: int
    nominal_fs_hz: int

    @property
    def identity_metadata(self):
        # These are versioned producer carriers, not an application version.
        return (self.provenance_revision + "|" + self.accounting_domain,
                self.artifact_sha256,
                f"producer:{self.profile_id}:{self.source_commit}:{self.expected_build_id:08x}")


# Artifact = the exact host/tests/test_stream_bridge.py file at source_commit.
# It is an encoded-byte test fixture. This declares a TEST interpretation of
# its counters; it neither changes that older fixture's default semantics nor
# proves any DMA/ADC behavior. No caller can register another profile at runtime.
SYNTHETIC_DMA_PROFILE = ProducerProfile(
    "syn1", DMA_DOMAIN, DMA_REVISION,
    "e86759a011b93a12a42542cfc9ba8149c673ff86",
    "c5377feac3cfbd97c5eef5fe2a82d772c0031d6a50e051ae1de24cf16ef7c0ca",
    0x1234, "synthetic", 1, 50000)

# Selected image identity, NOT an activated physical profile. PR207's archived
# source and ELF-derived BIN digest were checked; the original BIN was not
# recovered, and the unavailable full local Git tree remains attributed to A.
# Preserve the literal compiled-local ID rather than substituting public9ec943.
STR1_DIAGNOSTIC_DECLARATION = ProducerProfile(
    "str1", DMA_DOMAIN, DMA_REVISION,
    "31b93659164dec376bea5b973b597196c6a4a0ad",
    "90fee8a085d1e561cc6f5c28c26db26a43c1e57145ae41314bf70e9422c7b904",
    0x53545231, "device", 1, 50000)
STR1_DIAGNOSTIC_CAPS = (1, 1, 0, 12, 50000, 1, 128, 1)


def admit_fake_diagnostic_declaration(declaration):
    """Only the pinned declaration for a fake rehearsal; never real admission."""
    expected = STR1_DIAGNOSTIC_DECLARATION
    if (type(declaration) is not ProducerProfile or declaration != expected or
            any(type(getattr(declaration, f.name)) is not type(getattr(expected, f.name))
                for f in fields(ProducerProfile))):
        raise ProvenanceError("Не совпадает точная декларация fake-rehearsal str1")
    return expected


def validate_fake_diagnostic_negotiation(declaration, build_id, caps):
    """Compare already accepted wire observations before planning START/storage."""
    declaration = admit_fake_diagnostic_declaration(declaration)
    if type(build_id) is not int or build_id != declaration.expected_build_id:
        raise ProvenanceError("HELLO build ID не совпадает с fake-rehearsal str1")
    try:
        actual = tuple(getattr(caps, name) for name in
                       ("config_id", "profile", "channel", "adc_bits", "fs_num",
                        "fs_den", "max_block", "flags"))
    except AttributeError as exc:
        raise ProvenanceError("Нет принятых CAPS для fake-rehearsal str1") from exc
    if actual != STR1_DIAGNOSTIC_CAPS or any(type(value) is not int for value in actual):
        raise ProvenanceError("CAPS не совпадает с fake-rehearsal str1")


def rehearsal_identity_metadata(declaration):
    declaration = admit_fake_diagnostic_declaration(declaration)
    return (declaration.provenance_revision + "|" + declaration.accounting_domain,
            declaration.artifact_sha256,
            f"rehearsal:{declaration.profile_id}:{declaration.source_commit}:{declaration.expected_build_id:08x}")


def admit_profile(profile):
    """Return only the exact immutable declaration; no partial/build-ID lookup."""
    expected = SYNTHETIC_DMA_PROFILE
    if (type(profile) is not ProducerProfile or profile != expected or
            any(type(getattr(profile, f.name)) is not type(getattr(expected, f.name))
                for f in fields(ProducerProfile))):
        raise ProvenanceError("Неизвестная или несовпадающая декларация producer/domain")
    return expected


def validate_negotiation(profile, build_id, caps):
    """Check the caller's selection BEFORE reserving START or a recording."""
    profile = admit_profile(profile)
    if type(build_id) is not int or build_id != profile.expected_build_id:
        raise ProvenanceError("HELLO build ID не совпадает с выбранным producer profile")
    actual = (caps.config_id, caps.profile, caps.channel, caps.adc_bits,
              caps.fs_num, caps.fs_den, caps.max_block, caps.flags)
    if actual != (profile.config_id, 1, 0, 12, profile.nominal_fs_hz, 1, 128, 1):
        raise ProvenanceError("CAPS не совпадает с выбранным producer profile")


def profile_for_identity(identity):
    """Validate the durable declaration, including a fresh journal reopen.

    None means ONLY the pre-existing generic interpretation. A new-domain marker
    with absent/unknown/mixed fields is rejected, never downgraded to legacy.
    Hashes and HELLO build IDs remain declarations, not binary authentication.
    """
    revision, artifact, producer = (identity.protocol_revision,
                                    identity.firmware_version,
                                    identity.application_version)
    marked = ("dma-sequence" in (revision or "") or
              "completed-dma" in (revision or "") or
              (producer or "").startswith(("producer:", "rehearsal:")))
    if not marked:
        return None
    rehearsal = STR1_DIAGNOSTIC_DECLARATION
    if ((revision, artifact, producer) == rehearsal_identity_metadata(rehearsal) and
            identity.origin == "synthetic" and identity.config_id == rehearsal.config_id and
            identity.nominal_fs_hz == rehearsal.nominal_fs_hz):
        return rehearsal
    profile = SYNTHETIC_DMA_PROFILE
    if ((revision, artifact, producer) != profile.identity_metadata or
            identity.origin != profile.origin or identity.config_id != profile.config_id or
            identity.nominal_fs_hz != profile.nominal_fs_hz):
        raise ProvenanceError("Отсутствует или не совпадает сохранённая декларация producer/domain")
    return profile


def provenance_text(identity):
    """Portable visible meaning; used for live, recorded and no-R views."""
    profile = profile_for_identity(identity)
    if profile is None:
        return (f"Прежняя декларация: protocol {identity.protocol_revision}; "
                f"firmware {identity.firmware_version}; application {identity.application_version}. "
                "Новый DMA-домен не заявлен; исходный смысл метаданных сохранён.")
    if profile is STR1_DIAGNOSTIC_DECLARATION:
        return (f"СИНТЕТИЧЕСКАЯ репетиция декларации {profile.profile_id}; фактический origin synthetic, "
                f"предполагаемый origin device; NOT_FLASH_APPROVED. Домен {profile.accounting_domain}; "
                f"revision {profile.provenance_revision}; compiled-local Git {profile.source_commit}; "
                f"выбранный BIN SHA-256 {profile.artifact_sha256}; ожидаемый HELLO build {profile.expected_build_id:08x}. "
                "Сопоставление с ELF-derived digest не является восстановлением исходного BIN; "
                "совпадение HELLO/CAPS не аутентифицирует исполняемый образ. "
                "Счётчики относятся только к последовательности завершённых DMA-записей. "
                "ADC_DR overwrite, незавершённые преобразования и пропущенные триггеры не охвачены. "
                "Неизвестный produced_end остаётся неизвестным; Stop не доказывает нулевые потери. "
                "Время = индекс / номинальная Fs, не калиброванное физическое время.")
    return (f"СИНТЕТИЧЕСКАЯ декларация producer:{profile.profile_id}; домен {profile.accounting_domain}; "
            f"revision {profile.provenance_revision}; source Git {profile.source_commit}; "
            f"artifact SHA-256 {profile.artifact_sha256} (Python test fixture, не firmware image); "
            f"ожидаемый HELLO build {profile.expected_build_id:08x}. Совпадение build ID не аутентификация. "
            "Полнота и потери относятся только к последовательности завершённых DMA-записей. "
            "ADC_DR overwrite, запущенные, но не завершённые преобразования и пропущенные триггеры "
            "не входят в этот счётчик. Время = индекс / номинальная Fs, не калиброванное физическое время.")
