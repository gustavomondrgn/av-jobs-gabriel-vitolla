/**
 * A marca do AV Jobs — o SVG oficial, com as cores ligadas ao tema.
 *
 * O arquivo original é feito para fundo azul-marinho: o corpo do "A/V" é branco
 * (#FEFEFE). Colocado num painel de tema claro, ele simplesmente sumiria. Em
 * vez de manter duas artes ou empurrar um retângulo escuro atrás da logo, as
 * quatro cores viram tokens:
 *
 *   #1E74F6 / #1E76F9  → `--marca-anel`   (o azul, igual nos dois temas)
 *   #FEFEFE            → `--marca-corpo`  (branco no escuro, marinho no claro)
 *   #7BBBF8            → `--marca-faceta` (o azul claro da lateral do "A")
 *
 * O desenho é o mesmo; só a paleta responde ao tema.
 */
export function Marca({ tamanho = 28 }: { tamanho?: number }) {
  // A arte é bem mais alta que larga (563 × 845).
  const largura = Math.round(tamanho * (563 / 845));
  return (
    <svg
      width={largura}
      height={tamanho}
      viewBox="0 0 563 845"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      style={{ flexShrink: 0, display: 'block' }}
    >
      {/* Anel do alfinete */}
      <path
        d="M392.008 54.8267C326.158 22.7567 253.268 20.6867 185.478 48.3367C148.288 63.5067 115.368 87.0567 89.3675 117.537C53.3575 159.757 32.1875 212.717 29.2075 268.057C27.2975 303.397 34.1975 341.467 47.8675 373.877C53.4975 388.067 60.2875 401.077 68.6875 413.907L52.2375 441.057C41.4875 426.047 33.1775 410.747 25.3875 394.477C10.7975 362.787 2.49752 328.837 0.427522 293.977C-1.77248 256.817 4.60751 218.727 17.5075 184.087C24.9175 164.167 34.4875 145.797 45.8075 127.957C54.8575 113.697 64.9475 100.997 76.3875 88.7767L87.5175 77.6767C109.358 57.0067 134.088 40.2867 161.358 27.2467C195.838 10.7567 233.188 1.74668 271.428 0.216681C319.608 -1.71332 367.167 9.29667 409.967 31.3667C436.917 45.2567 461.028 63.0767 482.308 84.5767C498.178 100.597 511.708 118.047 523.178 137.497C546.018 176.247 560.238 219.397 562.698 264.387V292.687C561.938 306.257 560.518 319.237 557.838 332.677C550.158 371.217 534.278 407.367 511.788 439.447C511.428 439.967 511.278 440.597 510.878 440.647C510.508 440.697 510.138 440.107 509.868 439.667L494.298 413.767C518.568 376.117 533.058 331.107 534.168 286.187C535.228 243.407 524.868 201.017 504.798 163.097C491.828 138.597 474.758 116.557 454.618 97.6667C435.928 80.1367 415.028 66.0567 391.998 54.8367L392.008 54.8267Z"
        fill="var(--marca-anel)"
      />
      {/* "V" inferior */}
      <path
        d="M212.747 601.347L214.677 602.097L280.847 705.757C303.027 671.447 325.057 636.887 346.937 602.067L348.377 602.087L411.097 503.107L500.147 503.187L449.197 582.767L349.777 737.377L280.907 844.657L183.727 694.247L86.2666 543.217L60.4766 503.117L150.147 503.067L212.757 601.347H212.747Z"
        fill="var(--marca-corpo)"
      />
      {/* Triângulo de preenchimento entre as pernas do "V" */}
      <path
        d="M348.376 602.087L281.026 708.317L212.736 601.347H345.176C346.666 601.347 347.446 601.347 348.376 602.087Z"
        fill="var(--marca-anel)"
      />
      {/* "A" superior */}
      <path
        d="M324.707 186.007L324.937 188.057C310.727 212.667 296.527 237.277 282.327 261.877L280.887 261.917L211.737 378.447L297.607 378.397L319.267 415.457L368.017 498.217L322.027 570.987L266.447 476.877L242.657 436.677H176.507L152.137 476.557L64.2168 476.467L128.717 368.807L166.617 305.627L212.567 228.547L251.177 163.637L281.207 113.077L324.707 186.017V186.007Z"
        fill="var(--marca-corpo)"
      />
      {/* Faceta clara da perna direita do "A" */}
      <path
        d="M280.887 261.917L324.707 186.007L450.517 396.737L498.737 476.467L410.357 476.547L383.057 431.177L332.557 347.627L280.887 261.917Z"
        fill="var(--marca-faceta)"
      />
    </svg>
  );
}

export function MarcaCompleta({ tamanho = 30 }: { tamanho?: number }) {
  return (
    <span className="flex items-center gap-2.5" style={{ color: 'var(--texto)' }}>
      <Marca tamanho={tamanho} />
      <span className="leading-none">
        <span className="block text-[15px] font-bold tracking-tight">AV Jobs</span>
        <span
          className="block text-[10px] font-medium tracking-[0.14em] uppercase mt-0.5"
          style={{ color: 'var(--texto-fraco)' }}
        >
          Painel
        </span>
      </span>
    </span>
  );
}
