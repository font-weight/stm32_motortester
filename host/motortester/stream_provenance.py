"""Finite, declared counter semantics. No producer discovery or authentication.

The only admitted declaration describes an immutable SYNTHETIC Python peer
fixture, not a firmware image. There are no admitted physical-device profiles.
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
              (producer or "").startswith("producer:"))
    if not marked:
        return None
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
    return (f"СИНТЕТИЧЕСКАЯ декларация producer:{profile.profile_id}; домен {profile.accounting_domain}; "
            f"revision {profile.provenance_revision}; source Git {profile.source_commit}; "
            f"artifact SHA-256 {profile.artifact_sha256} (Python test fixture, не firmware image); "
            f"ожидаемый HELLO build {profile.expected_build_id:08x}. Совпадение build ID не аутентификация. "
            "Полнота и потери относятся только к последовательности завершённых DMA-записей. "
            "ADC_DR overwrite, запущенные, но не завершённые преобразования и пропущенные триггеры "
            "не входят в этот счётчик. Время = индекс / номинальная Fs, не калиброванное физическое время.")
