class Transmute < Formula
  include Language::Python::Virtualenv

  desc "Convert YouTube or SoundCloud links into rich MP3s"
  homepage "https://github.com/vznh/transmute"
  url "https://files.pythonhosted.org/packages/dd/a8/a6c5f8b481e8001656df18008b7d2876588b570e8c239fd05693876d2a41/transmute_cli-1.0.2.tar.gz"
  sha256 "7c38060c7cd6844b3f766ea235c40e0b245982ac30611dd03f06af8e074e4096"
  license "GPL-3.0-or-later"
  head "https://github.com/vznh/transmute.git", branch: "main"

  # Native Python resources are built from pinned sdists; these tools and
  # libraries satisfy their documented Rust, CMake, OpenSSL, image, and FFI builds.
  depends_on "rust" => :build
  depends_on "cmake" => :build
  depends_on "pkgconf" => :build
  depends_on "openssl@3"
  depends_on "jpeg-turbo"
  depends_on "zlib"
  depends_on "libffi"

  # Transmute shells out to ffmpeg for audio extraction and conversion.
  depends_on "ffmpeg"
  depends_on "python@3.13"

  resource "aiofiles" do
    url "https://files.pythonhosted.org/packages/41/c3/534eac40372d8ee36ef40df62ec129bee4fdb5ad9706e58a29be53b2c970/aiofiles-25.1.0.tar.gz"
    sha256 "a8d728f0a29de45dc521f18f07297428d56992a742f0cd2701ba86e44d23d5b2"
  end

  resource "annotated-doc" do
    url "https://files.pythonhosted.org/packages/5a/8e/38aa427ed5402449e226975b649c5dc73ccadfefeb95e6aecb8f8ea4b6b6/annotated_doc-0.0.5.tar.gz"
    sha256 "c7e58ce09192557605d8bbd92836d7e1d520ac9580096042c0bfd197efacf1bb"
  end

  resource "annotated-types" do
    url "https://files.pythonhosted.org/packages/5f/56/a8120250d128bed162cd73c76d45f6ef9991f3e068f62a8ee060afa3104a/annotated_types-0.8.0.tar.gz"
    sha256 "13b2beaad985e05e2d6407ee4c4f35590b11f8d693a258a561055cac8f64cab7"
  end

  resource "ansilogs" do
    url "https://files.pythonhosted.org/packages/6b/e0/4b3b453aec055d6cb3cce3650953a6fabc28e42e586d5400f1db41919398/ansilogs-1.1.0.tar.gz"
    sha256 "15f0e3581f9e14d0ddadd3795408878ef6d9023966607abadfa718f24baff882"
  end

  resource "anthropic" do
    url "https://files.pythonhosted.org/packages/d7/10/4ca013cb166f226bd89e0aeb0fcaff94f45ddf716d4925ce89475d3c587b/anthropic-0.120.2.tar.gz"
    sha256 "9722efc10c27a30a69f5338ddacdb35bc6a64297a4e4ba729bf83af873d5fb3a"
  end

  resource "anyio" do
    url "https://files.pythonhosted.org/packages/61/cc/a381afa6efea9f496eff839d4a6a1aed3bfafc7b3ab4b0d1b243a12573dd/anyio-4.14.2.tar.gz"
    sha256 "cfa139f3ed1a23ee8f88a145ddb5ac7605b8bbfd8592baacd7ce3d8bb4313c7f"
  end

  resource "apple-compress" do
    url "https://files.pythonhosted.org/packages/91/14/cdb0bdb6147a019d2d8a1e144910c6a8d257d0e58f63bbd105bda0fce7dc/apple_compress-0.2.3.tar.gz"
    sha256 "a1c855cec8bd73210ae9522c53a841ca515ded2f0c9d5b4e728d0d93fee46a29"
  end

  resource "arrow" do
    url "https://files.pythonhosted.org/packages/b9/33/032cdc44182491aa708d06a68b62434140d8c50820a087fac7af37703357/arrow-1.4.0.tar.gz"
    sha256 "ed0cc050e98001b8779e84d461b0098c4ac597e88704a655582b21d116e526d7"
  end

  resource "asgimiddlewarestaticfile" do
    url "https://files.pythonhosted.org/packages/d7/25/14ec3488ee466dd2b3c63e3cdb1f9b032d90c0c9d65ca68d25c59b74bc22/asgimiddlewarestaticfile-0.7.0.tar.gz"
    sha256 "70935ab3bb9cdde5cf96dcc4eed2fce39c9cf51ec371490265e673df099ddf72"
  end

  resource "asgiref" do
    url "https://files.pythonhosted.org/packages/6b/90/f5fce14e95fccf53b58e8e2c47cd84d40d105d64073ae1ac75166db150e0/asgiref-3.13.0.tar.gz"
    sha256 "034cc8cbf72d00553b4be256703ac545120b4e50af13b49dec99da899aeeecb4"
  end

  resource "asgiwebdav" do
    url "https://files.pythonhosted.org/packages/1d/56/dcf95ce6731a29752d2039b50b63296daeea9e63a0f48110a54f7dc02927/asgiwebdav-2.1.0.tar.gz"
    sha256 "d11ae797575b7cbe5be73d37ae99f839a68da39a70873e101dba33fbde6d0e2f"
  end

  resource "asn1" do
    url "https://files.pythonhosted.org/packages/00/35/9d0709bf09340ddb1796dfc52d1b1ef2f18e690b5cbf32b7be02c702532c/asn1-2.8.0.tar.gz"
    sha256 "adf77ddc2707cf420c0eae3b99ee30e913afcf0936467d42669820ce6b7d150a"
  end

  resource "asttokens" do
    url "https://files.pythonhosted.org/packages/25/1e/faf0f247f6f881b98fc4d6d07e14085cb89d13665084e6d6ac1dc2c03d0b/asttokens-3.0.2.tar.gz"
    sha256 "3ecdbd8f2cc195f53ccada3a613538bb5f9ef6f6869129f13e03c30a677b8fe2"
  end

  resource "backports-zstd" do
    url "https://files.pythonhosted.org/packages/ff/9c/13569626440e88f09d16f43ec1c2aa0d10a523be2811414580d1cfb7c9f3/backports_zstd-1.8.0.tar.gz"
    sha256 "9dae4f4c481716e3db473d667457b4f508ff7459c0931b567a5c9677fb3db316"
  end

  resource "bpylist2" do
    url "https://files.pythonhosted.org/packages/ca/34/eb90ff6be953f6e4df08d4e8c0b761bea144242b6d711e922113411cc631/bpylist2-4.1.1.tar.gz"
    sha256 "0cc63284aee42f5c7e0ec87f8f59cdd35aaed05ad12d866b1868ea0c0caaafe1"
  end

  resource "bytestruct" do
    url "https://files.pythonhosted.org/packages/1b/e4/37acaf4c2217e117c42163159204d749a318cb11dd58a5c39e37c482e720/bytestruct-0.2.0.tar.gz"
    sha256 "ef0767fe48c0be9b2e4585565d8b28959fa04c93256ddeb7efbbd1b8f5998011"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/a3/c2/24167ea9858356b47a87a50d39908bfdb72ceeefe0041586e704e5376b3a/certifi-2026.7.22.tar.gz"
    sha256 "741e2c3b351ddf169a738da9f2c048608ff7f2c5cc02f1ebc6b118bb090d5d55"
  end

  resource "cffi" do
    url "https://files.pythonhosted.org/packages/9e/ef/008a1939e372c06329a3fce4279c02f328488f3526744906eeec3da7ad5f/cffi-2.1.1.tar.gz"
    sha256 "dd31f52ea1086513bb9df30f8fcee9b8918323ae067a3d5b78bc826a000712be"
  end

  resource "chardet" do
    url "https://files.pythonhosted.org/packages/47/92/3d893ac5d338b9a9af34c5b517d77492dcea8a10254254eead4215a96f3f/chardet-7.7.0.tar.gz"
    sha256 "0ba38a05201b0abf7570c12d8af45c30b2af835bd5cc52bfc7105082eda82c1e"
  end

  resource "charset-normalizer" do
    url "https://files.pythonhosted.org/packages/33/1c/f41d4e74c28ab327ff3acd36053f7ea506c55872d7a90b0fa71aa3ab0c89/charset_normalizer-3.5.2.tar.gz"
    sha256 "39de2a259fc954455c57274dc94c79d5842774e1247a016aff30bc0efed0f4ef"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/c7/0e/7fa0ef50764b67090eca4114772a2abf8b6148198475e54c660b97caeee6/click-8.5.0.tar.gz"
    sha256 "ba0d2089de75ea0310e2dde03160e6ca10009947fb95a182f9b54021bb272e34"
  end

  resource "construct" do
    url "https://files.pythonhosted.org/packages/02/77/8c84b98eca70d245a2a956452f21d57930d22ab88cbeed9290ca630cf03f/construct-2.10.70.tar.gz"
    sha256 "4d2472f9684731e58cc9c56c463be63baa1447d674e0d66aeb5627b22f512c29"
  end

  resource "cryptography" do
    url "https://files.pythonhosted.org/packages/9d/af/182eb91b0df3fe75c4d9f26fe70684569566745f6ba7e5c9c73a862c5252/cryptography-50.0.2.tar.gz"
    sha256 "7b46165bb56eb4704e2eaaf86f3c940d19154535d9b0ca7d6d590b04060e00d5"
  end

  resource "daemonize" do
    url "https://files.pythonhosted.org/packages/8c/20/96f7dbc23812cfe4cf479c87af3e4305d0d115fd1fffec32ddeee7b9c82b/daemonize-2.5.0.tar.gz"
    sha256 "dd026e4ff8d22cb016ed2130bc738b7d4b1da597ef93c074d2adb9e4dea08bc3"
  end

  resource "dataclass-wizard" do
    url "https://files.pythonhosted.org/packages/b1/9d/465deff762411b97cc37fdd475402484724f695af042fa89ff221f725172/dataclass_wizard-1.0.0.tar.gz"
    sha256 "d458e6a97585377337f1633e5738ee1c051e84ca7726276ecde78d6534e485b5"
  end

  resource "defusedxml" do
    url "https://files.pythonhosted.org/packages/0f/d5/c66da9b79e5bdb124974bfe172b4daf3c984ebd9c2a06e2b8a4dc7331c72/defusedxml-0.7.1.tar.gz"
    sha256 "1bb3032db185915b62d7c6209c5a8792be6a32ab2fedacc84e01b52c51aa3e69"
  end

  resource "developer-disk-image" do
    url "https://files.pythonhosted.org/packages/52/f5/16c742e98191761d0925ed424468e3bec14503644c84851046602667d3c3/developer_disk_image-0.3.0.tar.gz"
    sha256 "1fb9841dcc5cf81e697b6e0a9c14909c059c1893d675b38398a91c08cb7ba8bc"
  end

  resource "distro" do
    url "https://files.pythonhosted.org/packages/fc/f8/98eea607f65de6527f8a2e8885fc8015d3e6f5775df186e443e0964a11c3/distro-1.9.0.tar.gz"
    sha256 "2fa77c6fd8940f116ee1d6b94a2f90b13b5ea8d019b98bc8bafdcabcdd9bdbed"
  end

  resource "docstring-parser" do
    url "https://files.pythonhosted.org/packages/e0/4d/f332313098c1de1b2d2ff91cf2674415cc7cddab2ca1b01ae29774bd5fdf/docstring_parser-0.18.0.tar.gz"
    sha256 "292510982205c12b1248696f44959db3cdd1740237a968ea1e2e7a900eeb2015"
  end

  resource "enum-compat" do
    url "https://files.pythonhosted.org/packages/41/46/8ed2368976d934652d8f33f1fdd86f5580fab45e890c90a848e83097a093/enum-compat-0.0.3.tar.gz"
    sha256 "3677daabed56a6f724451d585662253d8fb4e5569845aafa8bb0da36b1a8751e"
  end

  resource "executing" do
    url "https://files.pythonhosted.org/packages/52/92/8a611141e42930c0f194f7504063ab87dfe970cf1a5af28e7703df448734/executing-2.3.0.tar.gz"
    sha256 "15919cb5d667e5cb4e099511971d00d659573fff2dd5c4e6cd8b71636c7858d2"
  end

  resource "fastapi" do
    url "https://files.pythonhosted.org/packages/e9/eb/2cf8c80abf09b2800aef0a83b41066fbfda8ee5495b7a08b9d1af40f7046/fastapi-0.162.0.tar.gz"
    sha256 "6a22c1e2e6e32e0633ee7e170f00009a29b8228b16feb219ee42f32fd8d88a8f"
  end

  resource "h11" do
    url "https://files.pythonhosted.org/packages/01/ee/02a2c011bdab74c6fb3c75474d40b3052059d95df7e73351460c8588d963/h11-0.16.0.tar.gz"
    sha256 "4e35b956cf45792e4caa5885e69fba00bdbc6ffafbfa020300e549b208ee5ff1"
  end

  resource "httpcore" do
    url "https://files.pythonhosted.org/packages/06/94/82699a10bca87a5556c9c59b5963f2d039dbd239f25bc2a63907a05a14cb/httpcore-1.0.9.tar.gz"
    sha256 "6e34463af53fd2ab5d807f399a9b45ea31c3dfa2276f15a2c3f00afff6e176e8"
  end

  resource "httpx" do
    url "https://files.pythonhosted.org/packages/b1/df/48c586a5fe32a0f01324ee087459e112ebb7224f646c0b5023f5e79e9956/httpx-0.28.1.tar.gz"
    sha256 "75e98c5f16b0f35b567856f597f06ff2270a374470a5c2392242528e3e3e42fc"
  end

  resource "hyperframe" do
    url "https://files.pythonhosted.org/packages/02/e7/94f8232d4a74cc99514c13a9f995811485a6903d48e5d952771ef6322e30/hyperframe-6.1.0.tar.gz"
    sha256 "f630908a00854a7adeabd6382b43923a4c4cd4b821fcb527e6ab9e15382a3b08"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/cd/63/9496c57188a2ee585e0f1db071d75089a11e98aa86eb99d9d7618fc1edce/idna-3.18.tar.gz"
    sha256 "ffb385a7e039654cef1ab9ef32c6fafe283c0c0467bba1d9029738ce4a14a848"
  end

  resource "ifaddr" do
    url "https://files.pythonhosted.org/packages/e8/ac/fb4c578f4a3256561548cd825646680edcadb9440f3f68add95ade1eb791/ifaddr-0.2.0.tar.gz"
    sha256 "cc0cbfcaabf765d44595825fb96a99bb12c79716b73b44330ea38ee2b0c4aed4"
  end

  resource "ioregistry" do
    url "https://files.pythonhosted.org/packages/b0/97/9ca4605f8850c51c390103d2322df789210769bc6017f1d19c5d2b3bc3f3/ioregistry-0.0.6.tar.gz"
    sha256 "d7187ad5f93276f1ca981f72f8bee74f6bef4754d9a058643d0726b611844f32"
  end

  resource "ipsw-parser" do
    url "https://files.pythonhosted.org/packages/d5/82/60b20090243c258542b51364787ada12e4edd6191c6022731955546bc7e0/ipsw_parser-1.8.4.tar.gz"
    sha256 "72eda0985f1d39a8fe74385724598feaa2f95ed32cc519eae417d90addb9019d"
  end

  resource "ipython" do
    url "https://files.pythonhosted.org/packages/b9/32/99451b1283ec5d92ad77073f12e1c667dc10775384d8f15c2914207149dd/ipython-9.17.1.tar.gz"
    sha256 "8919be8c27f20a6f4423145028063f6637b42a03ce57665bb12015ee1f073529"
  end

  resource "ipython-pygments-lexers" do
    url "https://files.pythonhosted.org/packages/ef/4c/5dd1d8af08107f88c7f741ead7a40854b8ac24ddf9ae850afbcf698aa552/ipython_pygments_lexers-1.1.1.tar.gz"
    sha256 "09c0138009e56b6854f9535736f4171d855c8c08a563a0dcd8022f78355c7e81"
  end

  resource "jedi" do
    url "https://files.pythonhosted.org/packages/a3/a4/4692ecd7fa700ac1066b1c1f6cadef60aa2e01e5e364710235137d823e0a/jedi-0.20.1.tar.gz"
    sha256 "2f71208c3f9c1bca057c0e90d3f272aba44ace88fc4067d7587e9e069331b7e5"
  end

  resource "jiter" do
    url "https://files.pythonhosted.org/packages/1d/1f/10936e16d8860c70698a1aa939a46aa0224813b782bce4e000e637da0b2d/jiter-0.16.0.tar.gz"
    sha256 "7b24c3492c5f4f84a37946ad9cf504910cf6a782d6a4e0689b6673c5894b4a1c"
  end

  resource "loguru" do
    url "https://files.pythonhosted.org/packages/3a/05/a1dae3dffd1116099471c643b8924f5aa6524411dc6c63fdae648c4f1aca/loguru-0.7.3.tar.gz"
    sha256 "19480589e77d47b8d85b2c827ad95d49bf31b0dcde16593892eb51dd18706eb6"
  end

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/06/ff/7841249c247aa650a76b9ee4bbaeae59370dc8bfd2f6c01f3630c35eb134/markdown_it_py-4.2.0.tar.gz"
    sha256 "04a21681d6fbb623de53f6f364d352309d4094dd4194040a10fd51833e418d49"
  end

  resource "matplotlib-inline" do
    url "https://files.pythonhosted.org/packages/bd/c0/9f7c9a46090390368a4d7bcb76bb87a4a36c421e4c0792cdb53486ffac7a/matplotlib_inline-0.2.2.tar.gz"
    sha256 "72f3fe8fce36b70d4a5b612f899090cd0401deddc4ea90e1572b9f4bfb058c79"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/d6/54/cfe61301667036ec958cb99bd3efefba235e65cdeb9c84d24a8293ba1d90/mdurl-0.1.2.tar.gz"
    sha256 "bb413d29f5eea38f31dd4754dd7377d4465116fb207585f97bf925588687c1ba"
  end

  resource "mutagen" do
    url "https://files.pythonhosted.org/packages/df/70/1675da133ea92227da41bf5b24e1c66be597ff736a1533ade41da986852f/mutagen-1.48.1.tar.gz"
    sha256 "8f95637ab9f6f305cec6bd1294e197debe207998e3e068596563c74f86b0a173"
  end

  resource "opack2" do
    url "https://files.pythonhosted.org/packages/5d/8a/ac48f032c6e6fbb27733eae8dc51760cae02ad3c3be4252b124d38621fac/opack2-0.1.0.tar.gz"
    sha256 "6164a950fba950a440696c77fe69f459288f76c26c7a3cff5f30e54aee9aa6ed"
  end

  resource "openai" do
    url "https://files.pythonhosted.org/packages/d8/f5/e7735f2af272ee179a287911a698b3cbdb59d7a4ac4874571363adf1e4de/openai-2.50.0.tar.gz"
    sha256 "5128f7caf4a6b01aefd6e7e93efe170a2c3427b8de286b9af5cdff3aa47e02c8"
  end

  resource "opentelemetry-api" do
    url "https://files.pythonhosted.org/packages/2e/02/6e0ae9cc61bd3169d401077b507b3ebc344745171e1051ab430be012dcd9/opentelemetry_api-1.45.1.tar.gz"
    sha256 "aa38ed19bcc084ba42782a73255b3582283eced7ad6dddbd6695189e69adfb75"
  end

  resource "packaging" do
    url "https://files.pythonhosted.org/packages/d7/f1/e7a6dd94a8d4a5626c03e4e99c87f241ba9e350cd9e6d75123f992427270/packaging-26.2.tar.gz"
    sha256 "ff452ff5a3e828ce110190feff1178bb1f2ea2281fa2075aadb987c2fb221661"
  end

  resource "parso" do
    url "https://files.pythonhosted.org/packages/30/4b/90c937815137d43ce71ba043cd3566221e9df6b9c805f24b5d138c9d40a7/parso-0.8.7.tar.gz"
    sha256 "eaaac4c9fdd5e9e8852dc778d2d7405897ec510f2a298071453e5e3a07914bb1"
  end

  resource "pexpect" do
    url "https://files.pythonhosted.org/packages/42/92/cc564bf6381ff43ce1f4d06852fc19a2f11d180f23dc32d9588bee2f149d/pexpect-4.9.0.tar.gz"
    sha256 "ee7d41123f3c9911050ea2c2dac107568dc43b2d3b0c7557a33212c398ead30f"
  end

  resource "pillow" do
    url "https://files.pythonhosted.org/packages/ce/a4/9cc30690a394a8305e8be4c9036aa16d0dfddadd09079860b582f562496d/pillow-13.0.0.tar.gz"
    sha256 "cdcd1ba258178f7356d74170c9a81ba4eab6a5d55748bc402a5a2a6fe6be3510"
  end

  resource "pmd-net-addr" do
    url "https://files.pythonhosted.org/packages/ce/bb/e24ffb9af4243050b54fbaf08393879456f1bd98996b66c6df509106ad2b/pmd_net_addr-0.0.3.tar.gz"
    sha256 "1b0fcb3b77f30076237360eb4cf0243483278a2650fd4a194985cc69b1de07e5"
  end

  resource "pmd-net-proto" do
    url "https://files.pythonhosted.org/packages/59/ec/eed1b9570cec3e30b993dee84fa5d16be3399189ffce8c0b46373727d20e/pmd_net_proto-0.0.3.tar.gz"
    sha256 "e23d7d68cd13399ff2bf0613f9756e221ead6fd54010cf85186c2ab650631cd7"
  end

  resource "pmd-pytcp" do
    url "https://files.pythonhosted.org/packages/f8/74/e9a79796df33932558913d309fba9015080c4e241cdb61e6280421db4be7/pmd_pytcp-0.3.8.tar.gz"
    sha256 "a26e76e735a04bb6bbcec0793aadcd4f20f03778fd29971a3e2747ce394e9795"
  end

  resource "prompt-toolkit" do
    url "https://files.pythonhosted.org/packages/7d/ea/39b988c938f75cb75d7045b5c69f8bfed47ee2152c8837fb403de29d6fb8/prompt_toolkit-3.0.53.tar.gz"
    sha256 "9ec8a0ad96d5c56148b3f914aa79c1564c3fde5d2e6b876e7bc327e353cf8fa6"
  end

  resource "psutil" do
    url "https://files.pythonhosted.org/packages/aa/c6/d1ddf4abb55e93cebc4f2ed8b5d6dbad109ecb8d63748dd2b20ab5e57ebe/psutil-7.2.2.tar.gz"
    sha256 "0746f5f8d406af344fd547f1c8daa5f5c33dbc293bb8d6a16d80b4bb88f59372"
  end

  resource "ptyprocess" do
    url "https://files.pythonhosted.org/packages/20/e5/16ff212c1e452235a90aeb09066144d0c5a6a8c0834397e03f5224495c4e/ptyprocess-0.7.0.tar.gz"
    sha256 "5c5d0a3b48ceee0b48485e0c26037c0acd7d29765ca3fbb5cb3831d347423220"
  end

  resource "pure-eval" do
    url "https://files.pythonhosted.org/packages/da/9f/abfd2959e9261dd5217ca8551d4de211ca6ab26fe9b72cf44731ff6c4442/pure_eval-0.2.4.tar.gz"
    sha256 "260c2774686e651b79f8b8e7fc9d80b3599ea6a66334b47d5f4abb69fc2c0ea1"
  end

  resource "pycparser" do
    url "https://files.pythonhosted.org/packages/cb/27/eeb9f307bf07060f3d9f9aa0fb8c15b01bdfded0a941e4b2719c7f94cacd/pycparser-3.12.tar.gz"
    sha256 "f03387877eab82ce01bbdfd0ba7252ee55a90b1c708d5129e2ce915475cf1db6"
  end

  resource "pycrashreport" do
    url "https://files.pythonhosted.org/packages/aa/9c/1e1c2d84f746972cbe4d5e65f816008c64e427fc680f55115aa313683c23/pycrashreport-2.0.0.tar.gz"
    sha256 "31d5e32faa3a047fe01e923bde3eaf1ec86b23e264babf2fc8f7b61fe4812342"
  end

  resource "pycryptodome" do
    url "https://files.pythonhosted.org/packages/33/c7/f9374c8939560ab22db1dd9dbe215b62d20f32634aeb7f259dc03c807b44/pycryptodome-4.2.0.tar.gz"
    sha256 "21d7fdba239d6ef863d3d08388801c683453a3bb4897c2b27f27847aad89bdf7"
  end

  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/18/a5/b60d21ac674192f8ab0ba4e9fd860690f9b4a6e51ca5df118733b487d8d6/pydantic-2.13.4.tar.gz"
    sha256 "c40756b57adaa8b1efeeced5c196f3f3b7c435f90e84ea7f443901bec8099ef6"
  end

  resource "pydantic-core" do
    url "https://files.pythonhosted.org/packages/9d/56/921726b776ace8d8f5db44c4ef961006580d91dc52b803c489fafd1aa249/pydantic_core-2.46.4.tar.gz"
    sha256 "62f875393d7f270851f20523dd2e29f082bcc82292d66db2b64ea71f64b6e1c1"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/c3/b2/bc9c9196916376152d655522fdcebac55e66de6603a76a02bca1b6414f6c/pygments-2.20.0.tar.gz"
    sha256 "6757cd03768053ff99f3039c1a36d6c0aa0b263438fcab17520b30a303a82b5f"
  end

  resource "pygnuutils" do
    url "https://files.pythonhosted.org/packages/03/20/6a828fe03d6586241b824ee06c7da854ea351716006a62fd2548eaa5710b/pygnuutils-0.1.1.tar.gz"
    sha256 "51ab4f27deb59102b7a04192f7585ff2b3a9de03739982f0b19d3cd4d16bea76"
  end

  resource "pyimg4" do
    url "https://files.pythonhosted.org/packages/3c/5c/69bad79b4e9d7be46063e9ea15e82c79ae0dec90811501453003588aab62/pyimg4-0.8.8.tar.gz"
    sha256 "aafebb2bc7922f6cf551b1b9e982d2da039cbeb08f0a2e2048a1818d51c76da1"
  end

  resource "pyiosbackup" do
    url "https://files.pythonhosted.org/packages/46/ab/c7c3caf6785ced43d02c8c37be3c84720c40d6a9b237eb2ff560af4114e1/pyiosbackup-0.2.4.tar.gz"
    sha256 "10b4d2a11c9bedc93a5467cad3addbe18bc2dc434156920e7223226ed4d0beb7"
  end

  resource "pykdebugparser" do
    url "https://files.pythonhosted.org/packages/13/77/fc89236fb1d87cafe5d822f55b4d12bdbf291743b496041f37923ffc8aeb/pykdebugparser-1.10.0.tar.gz"
    sha256 "d21c95d25e36e53b0a0542233bedfec68adf9e8f170bdf3da992e3837664263e"
  end

  resource "pylzss" do
    url "https://files.pythonhosted.org/packages/7d/dc/9ae75ede398b7adf538f2d1dca0f96c645c4e96789f8039340a0ed6a8a8f/pylzss-0.3.4.tar.gz"
    sha256 "16818631742488e53a34fda0d402d80edb2b812e11877801e21a9e5ce9b9db1c"
  end

  resource "pymobiledevice3" do
    url "https://files.pythonhosted.org/packages/b0/32/47e3a8b38e139983b69432dd264fa972fba7fa62087d18538d192328c11c/pymobiledevice3-11.45.0.tar.gz"
    sha256 "19ed34bdc02e32f2006f5539a86d5d2e4b4f1ae45200cf516ca9348d6f50a4c8"
  end

  resource "python-dateutil" do
    url "https://files.pythonhosted.org/packages/0e/df/f15f80383f152452d8985b36d95cb8751f901a3e0ff4cd27f7e118f168e2/python_dateutil-3.0.0.tar.gz"
    sha256 "7ebcb3c29a92f3ceafe79a7a48a5c45677e9e004bbeec5742ac0bbb00560b7a2"
  end

  resource "python-pcapng" do
    url "https://files.pythonhosted.org/packages/3c/2f/72534730a81205cd3ca32d65b4760c927e6ffc69a3d06ff3d1edda64ae3e/python-pcapng-2.1.1.tar.gz"
    sha256 "6477e72513165954e56d5836ebbd43ac7f8d28c4640f78d63d6506d1692ddc74"
  end

  resource "pytun-pmd3" do
    url "https://files.pythonhosted.org/packages/5e/8a/6e73c70ddf5fdf9cf359cdc458f38cf795c4a653c870a4a49c8768e5db86/pytun_pmd3-3.0.3.tar.gz"
    sha256 "7fa822092139b4b77e08fad9233dbd4a6fcca2cf2423b0775db962ca5e87a017"
  end

  resource "pyusb" do
    url "https://files.pythonhosted.org/packages/00/6b/ce3727395e52b7b76dfcf0c665e37d223b680b9becc60710d4bc08b7b7cb/pyusb-1.3.1.tar.gz"
    sha256 "3af070b607467c1c164f49d5b0caabe8ac78dbed9298d703a8dbf9df4052d17e"
  end

  resource "qh3" do
    url "https://files.pythonhosted.org/packages/72/ae/9d42d6df0ab9a014138332346fd690f7b0be0556861421d2459caec28d6f/qh3-1.9.4.tar.gz"
    sha256 "bd2ea9baf19656c544a48a56a195f2ac257cd973b566f5f2998fa3b7446281a1"
  end

  resource "questionary" do
    url "https://files.pythonhosted.org/packages/f6/45/eafb0bba0f9988f6a2520f9ca2df2c82ddfa8d67c95d6625452e97b204a5/questionary-2.1.1.tar.gz"
    sha256 "3d7e980292bb0107abaa79c68dd3eee3c561b83a0f89ae482860b181c8bd412d"
  end

  resource "remotezip2" do
    url "https://files.pythonhosted.org/packages/3f/47/94335875a4d5339f64943c1a68900f59a91ad987ab8fcd8e5201839e7cc5/remotezip2-0.0.2.tar.gz"
    sha256 "db38fb14d0c297af6da8756808bb25f9d3b2c23c7639a14f7a74794b2fc9a261"
  end

  resource "requests" do
    url "https://files.pythonhosted.org/packages/ac/c3/e2a2b89f2d3e2179abd6d00ebd70bff6273f37fb3e0cc209f48b39d00cbf/requests-2.34.2.tar.gz"
    sha256 "f288924cae4e29463698d6d60bc6a4da69c89185ad1e0bcc4104f584e960b9ed"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/c0/8f/0722ca900cc807c13a6a0c696dacf35430f72e0ec571c4275d2371fca3e9/rich-15.0.0.tar.gz"
    sha256 "edd07a4824c6b40189fb7ac9bc4c52536e9780fbbfbddf6f1e2502c31b068c36"
  end

  resource "shellingham" do
    url "https://files.pythonhosted.org/packages/58/15/8b3609fd3830ef7b27b655beb4b4e9c62313a4e8da8c676e142cc210d58e/shellingham-1.5.4.tar.gz"
    sha256 "8dbca0739d487e5bd35ab3ca4b36e11c4078f3a234bfce294b0a0291363404de"
  end

  resource "six" do
    url "https://files.pythonhosted.org/packages/94/e7/b2c673351809dca68a0e064b6af791aa332cf192da575fd474ed7d6f16a2/six-1.17.0.tar.gz"
    sha256 "ff70335d468e7eb6ec65b95b99d3a2836546063f63acc5171de367e834932a81"
  end

  resource "sniffio" do
    url "https://files.pythonhosted.org/packages/a2/87/a6771e1546d97e7e041b6ae58d80074f81b7d5121207425c964ddf5cfdbd/sniffio-1.3.1.tar.gz"
    sha256 "f4324edc670a0f49750a81b895f35c3adb843cca46f0530f79fc1babb23789dc"
  end

  resource "srptools" do
    url "https://files.pythonhosted.org/packages/7c/70/eeb984c83b1313d8222a7ced3d764f250f76601a9bb8460d2a14c1d7faaf/srptools-2.0.0.tar.gz"
    sha256 "3b5296fde4b70e76443bbeacc641f4e4125c81b6bd20e4e228bba340f00c06c5"
  end

  resource "stack-data" do
    url "https://files.pythonhosted.org/packages/28/e3/55dcc2cfbc3ca9c29519eb6884dd1415ecb53b0e934862d3559ddcb7e20b/stack_data-0.6.3.tar.gz"
    sha256 "836a778de4fec4dcd1dcd89ed8abff8a221f58308462e1c4aa2a3cf30148f0b9"
  end

  resource "starlette" do
    url "https://files.pythonhosted.org/packages/48/bb/3d8757f21e9a53d9035c9eb039a777047139a16bfdbedfc7a58241c3d914/starlette-1.10.0.tar.gz"
    sha256 "b8399098d4c1e3522734f7235dc79b1fc967f96cf139c81e0fa30479e0685f9b"
  end

  resource "termcolor" do
    url "https://files.pythonhosted.org/packages/46/79/cf31d7a93a8fdc6aa0fbb665be84426a8c5a557d9240b6239e9e11e35fc5/termcolor-3.3.0.tar.gz"
    sha256 "348871ca648ec6a9a983a13ab626c0acce02f515b9e1983332b17af7979521c5"
  end

  resource "tqdm" do
    url "https://files.pythonhosted.org/packages/21/3b/6c24bec5be5e743ffd99576daa5cc077722fc7d5bbc00bd133fa0c698dc6/tqdm-4.70.0.tar.gz"
    sha256 "55b0b0dbd97462d06ebee91e4dac24ed4d4702be82b24f07e6c1d27e08cea220"
  end

  resource "traitlets" do
    url "https://files.pythonhosted.org/packages/2c/2e/a7fbfe268c8a3b32546930c0297c101d65a4a14c304ad5790a9f478f0e4e/traitlets-5.16.1.tar.gz"
    sha256 "ed900c2b631aa3a112811139fa97b8d2c3bad5e989656bba4b7e52c7852c18c1"
  end

  resource "typer" do
    url "https://files.pythonhosted.org/packages/03/51/d33db42cc72ffd8c30777547b42d01f0cbf9d95a770457698d0174b3ed71/typer-0.27.3.tar.gz"
    sha256 "d0396f770a560ab1b0a8504e13b5f254b728cedb05c61cf0359e944e50ce8901"
  end

  resource "typer-injector" do
    url "https://files.pythonhosted.org/packages/b3/11/7da0c6019c26cb660acc3329b44c3247286469de3dcd94013d0793598ecb/typer_injector-0.3.0.tar.gz"
    sha256 "7fa5b411b86c31aeeb22fc180d0ebf615c2a71c8f2b1dc97ad4d06a4c235b3a5"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/f6/cc/6253133b5bb138fc3306cebfbda2c520f545d36b5be2c7255cc528bb45d6/typing_extensions-4.16.0.tar.gz"
    sha256 "dc983d19a509c94dba722ee6abd33940f7c05a89e243c47e907eb4db6f1a43e5"
  end

  resource "typing-inspection" do
    url "https://files.pythonhosted.org/packages/55/e3/70399cb7dd41c10ac53367ae42139cf4b1ca5f36bb3dc6c9d33acdb43655/typing_inspection-0.4.2.tar.gz"
    sha256 "ba561c48a67c5958007083d386c3295464928b01faa735ab8547c5692e87f464"
  end

  resource "tzdata" do
    url "https://files.pythonhosted.org/packages/d9/68/f1b440335057bfce71b6e50a9d09445aa2ecbd08359a337976627b8409e7/tzdata-2026.5.tar.gz"
    sha256 "8cc73c0a0bfca7dbfa59235d60b2eff82231dee33f53d206db1acd9173cfc0a7"
  end

  resource "urllib3" do
    url "https://files.pythonhosted.org/packages/e3/05/b17359e1cefb4f909b5e40b1b90a496d987258916dbbf88e842c729f510e/urllib3-2.8.0.tar.gz"
    sha256 "63bf2ead4c879426ebf22ef2a781eeb4aa3b4ae798a0435506f8687fd5bb9b63"
  end

  resource "uvicorn" do
    url "https://files.pythonhosted.org/packages/da/34/30e9280707135d2cfc589dfff3cb796bd07a3aeb1a3e415ba09dd89d7bb4/uvicorn-0.54.0.tar.gz"
    sha256 "a2e33cbfaa0306f8e6b0c13e0cb89d7d7a2da3e62b90c66e18c33d9807b28620"
  end

  resource "wcwidth" do
    url "https://files.pythonhosted.org/packages/34/74/c6428f875774288bec1396f5bfcbc2d925700a4dad61727fd5f2b12f249d/wcwidth-0.8.2.tar.gz"
    sha256 "91fbef97204b96a3d4d421609b80340b760cf33e26da123ff243d76b1fda8dda"
  end

  resource "wsproto" do
    url "https://files.pythonhosted.org/packages/c7/79/12135bdf8b9c9367b8701c2c19a14c913c120b882d50b014ca0d38083c2c/wsproto-1.3.2.tar.gz"
    sha256 "b86885dcf294e15204919950f666e06ffc6c7c114ca900b060d6e16293528294"
  end

  resource "xmltodict" do
    url "https://files.pythonhosted.org/packages/19/70/80f3b7c10d2630aa66414bf23d210386700aa390547278c789afa994fd7e/xmltodict-1.0.4.tar.gz"
    sha256 "6d94c9f834dd9e44514162799d344d815a3a4faec913717a9ecbfa5be1bb8e61"
  end

  resource "yt-dlp" do
    url "https://files.pythonhosted.org/packages/1e/e0/832fa4ca334b766a06933a196066edc3dba37cdb6f14cd98d59bcc69a4b4/yt_dlp-2026.8.19.tar.gz"
    sha256 "9e213e48cea35c66b378e4447903f118f6392a5fa380a2b6d7070ec86f4e0af1"
  end

  def install
    ENV["OPENSSL_DIR"] = Formula["openssl@3"].opt_prefix.to_s
    venv = virtualenv_create(libexec, "python3.13")
    resources.each do |resource|
      if resource.name == "qh3"
        # AWS-LC's jitterentropy rejects optimization; superenv replaces its -O0.
        ENV.O0 { venv.pip_install resource }
      else
        venv.pip_install resource
      end
    end
    venv.pip_install_and_link buildpath
  end

  test do
    # Downloading real media needs the network and a source site, so the test
    # only proves the console script and its virtualenv resolve and run.
    #
    # The literal version is asserted rather than Homebrew's `version`, which is
    # parsed from the source archive URL while the console script reports
    # __version__ verbatim.
    assert_match "transmute 1.0.2", shell_output("#{bin}/transmute --version")
    assert_match "usage: transmute", shell_output("#{bin}/transmute --help")
  end
end
